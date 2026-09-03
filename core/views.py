import json
from django.conf import settings
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
import razorpay
from .models import FoodItem, Order, OrderItem, Theater
from django.views.decorators.csrf import csrf_exempt


def login_view(request):
  seat = request.GET.get('seat') or request.POST.get('seat')
  theater_id = request.GET.get('theater') or request.POST.get('theater')

  if seat and theater_id:
    request.session['seat'] = seat
    request.session['theater_id'] = theater_id

  # Enforce QR code scan requirement
  if not request.session.get('seat') or not request.session.get('theater_id'):
    return HttpResponseForbidden(
        'Access Denied: You must scan a valid theater QR code to access this'
        ' ordering portal.'
    )

  theater = get_object_or_404(Theater, id=request.session.get('theater_id'))

  if request.method == 'POST':
    name = request.POST.get('name')
    phone = request.POST.get('phone')
    if name and phone:
      request.session['customer_name'] = name
      request.session['customer_phone'] = phone
      return redirect('client_menu')

  return render(
      request,
      'login.html',
      {'theater': theater, 'seat': request.session.get('seat')},
  )


def client_menu(request):
  if not request.session.get('seat') or not request.session.get('theater_id'):
    return redirect('login')

  theater_id = request.session.get('theater_id')
  theater = get_object_or_404(Theater, id=theater_id)

  category = request.GET.get('category', 'ALL')
  search_query = request.GET.get('q', '')

  items = FoodItem.objects.filter(theater=theater)
  if category != 'ALL':
    items = items.filter(category=category)
  if search_query:
    items = items.filter(
        Q(name__icontains=search_query) | Q(unit_spec__icontains=search_query)
    )

  return render(
      request,
      'client_menu.html',
      {
          'items': items,
          'theater': theater,
          'seat': request.session.get('seat'),
          'customer_name': request.session.get('customer_name'),
          'current_category': category,
          'search_query': search_query,
      },
  )


def my_orders_view(request):
  if not request.session.get('seat') or not request.session.get('theater_id'):
    return redirect('login')

  customer_phone = request.session.get('customer_phone')
  order_history = (
      Order.objects.filter(customer_phone=customer_phone)
      .exclude(status='PENDING')
      .order_by('-created_at')
      if customer_phone
      else []
  )

  return render(
      request,
      'my_orders.html',
      {'order_history': order_history, 'customer_phone': customer_phone},
  )


def checkout_view(request):
  if not request.session.get('seat') or not request.session.get('theater_id'):
    return redirect('login')
  theater = Theater.objects.filter(id=request.session.get('theater_id')).first()
  return render(
      request,
      'checkout.html',
      {
          'seat': request.session.get('seat', 'default'),
          'theater_name': theater.name if theater else 'Theater',
      },
  )


@transaction.atomic
def create_direct_upi_order(request):
  if request.method == 'POST':
    try:
      data = json.loads(request.body)
      items_data = data.get('items', [])
      payment_method = data.get('payment_method', 'ONLINE')
      fulfillment_type = data.get('fulfillment_type', 'SEAT')

      theater_id = request.session.get('theater_id', 1)
      theater = get_object_or_404(Theater, id=theater_id)
      customer_name = request.session.get('customer_name', 'Guest')
      customer_phone = request.session.get('customer_phone', '0000000000')
      seat_number = request.session.get('seat', 'default')

      subtotal = 0
      for item in items_data:
        food_item = get_object_or_404(FoodItem, id=item['id'])
        qty = int(item['qty'])

        if food_item.quantity_available < qty:
          return JsonResponse(
              {
                  'error': (
                      f'Sorry, "{food_item.name}" only has'
                      f' {food_item.quantity_available} left in stock.'
                  )
              },
              status=400,
          )

        subtotal += float(food_item.price) * qty

      delivery_fee = 10 if fulfillment_type == 'SEAT' else 0
      total_amount = float(subtotal) + delivery_fee
      amount_in_paise = int(total_amount * 100)

      if amount_in_paise < 100:
        return JsonResponse(
            {'error': 'Minimum order amount must be at least ₹1.'}, status=400
        )

      # If CASH: Create order immediately in DB
      if payment_method == 'CASH':
        order = Order.objects.create(
            theater=theater,
            customer_name=customer_name,
            customer_phone=customer_phone,
            seat_number=seat_number,
            fulfillment_type=fulfillment_type,
            payment_method='CASH',
            total_amount=total_amount,
            status='CONFIRMED',
            is_paid=False,
        )

        for item in items_data:
          food_item = FoodItem.objects.select_for_update().get(id=item['id'])
          qty = int(item['qty'])
          OrderItem.objects.create(
              order=order,
              food_item=food_item,
              quantity=qty,
              price=food_item.price,
          )
          food_item.quantity_available -= qty
          if food_item.quantity_available < 0:
            food_item.quantity_available = 0
          food_item.save()

        return JsonResponse({'status': 'CASH_SUCCESS', 'order_id': order.id})

      # If ONLINE: Do NOT create Order in DB yet. Initialize Razorpay order and cache cart in session using primitives.
      client = razorpay.Client(
          auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
      )
      razorpay_order = client.order.create({
          'amount': amount_in_paise,
          'currency': 'INR',
          'receipt': f'rcpt_{theater_id}_{customer_phone}',
          'payment_capture': 1,
      })

      # Save cart details temporarily in session using primitives to prevent serialization errors
      request.session['pending_order_data'] = {
          'items': items_data,
          'fulfillment_type': fulfillment_type,
          'total_amount': float(total_amount),
          'razorpay_order_id': str(razorpay_order['id']),
      }
      request.session.modified = True

      return JsonResponse({
          'status': 'RAZORPAY_ORDER_CREATED',
          'razorpay_order_id': razorpay_order['id'],
          'razorpay_merchant_key': settings.RAZORPAY_KEY_ID,
          'total_amount': amount_in_paise,
          'currency': 'INR',
      })
    except Exception as e:
      return JsonResponse({'error': str(e)}, status=500)


@csrf_exempt
@transaction.atomic
def verify_razorpay_payment(request):
  if request.method == 'POST':
    try:
      data = json.loads(request.body)
      razorpay_order_id = data.get('razorpay_order_id')
      razorpay_payment_id = data.get('razorpay_payment_id')
      razorpay_signature = data.get('razorpay_signature')

      client = razorpay.Client(
          auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
      )
      client.utility.verify_payment_signature({
          'razorpay_order_id': razorpay_order_id,
          'razorpay_payment_id': razorpay_payment_id,
          'razorpay_signature': razorpay_signature,
      })

      pending_data = request.session.get('pending_order_data')
      if (
          not pending_data
          or pending_data.get('razorpay_order_id') != razorpay_order_id
      ):
        return JsonResponse(
            {
                'status': 'FAILURE',
                'error': 'Session expired or payment mismatch.',
            },
            status=400,
        )

      theater_id = request.session.get('theater_id', 1)
      theater = get_object_or_404(Theater, id=theater_id)
      customer_name = request.session.get('customer_name', 'Guest')
      customer_phone = request.session.get('customer_phone', '0000000000')
      seat_number = request.session.get('seat', 'default')

      items_data = pending_data['items']
      fulfillment_type = pending_data['fulfillment_type']
      total_amount = pending_data['total_amount']

      # Create order only now that payment has succeeded and signature is verified
      order = Order.objects.create(
          theater=theater,
          customer_name=customer_name,
          customer_phone=customer_phone,
          seat_number=seat_number,
          fulfillment_type=fulfillment_type,
          payment_method='ONLINE',
          total_amount=total_amount,
          status='CONFIRMED',
          is_paid=True,
          transaction_id=razorpay_payment_id,
          razorpay_order_id=razorpay_order_id,
      )

      for item in items_data:
        food_item = FoodItem.objects.select_for_update().get(id=item['id'])
        qty = int(item['qty'])
        if food_item.quantity_available < qty:
          raise Exception(
              f'Sorry, "{food_item.name}" went out of stock during payment'
              ' processing.'
          )

        OrderItem.objects.create(
            order=order,
            food_item=food_item,
            quantity=qty,
            price=food_item.price,
        )
        food_item.quantity_available -= qty
        food_item.save()

      # Clear session cache
      if 'pending_order_data' in request.session:
        del request.session['pending_order_data']

      return JsonResponse({'status': 'SUCCESS', 'order_id': order.id})
    except Exception as e:
      return JsonResponse({'status': 'FAILURE', 'error': str(e)}, status=400)
  return HttpResponseBadRequest('Invalid request method')


def order_confirmed(request, order_id):
  order = get_object_or_404(Order, id=order_id)
  return render(request, 'order_confirmed.html', {'order': order})


def shopkeeper_login_view(request, theater_id):
  theater = get_object_or_404(Theater, id=theater_id)
  error = None

  if request.method == 'POST':
    username = request.POST.get('username')
    password = request.POST.get('password')

    user = authenticate(request, username=username, password=password)
    if user is not None:
      login(request, user)
      return redirect('shopkeeper_dashboard')
    else:
      error = 'Invalid username or password.'

  return render(
      request,
      'shopkeeper_login.html',
      {'theater': theater, 'error': error},
  )


@login_required
def shopkeeper_dashboard_view(request):
  theater = Theater.objects.first()
  active_tab = request.GET.get('tab', 'live')

  if request.method == 'POST' and 'update_order_status' in request.POST:
    order_id = request.POST.get('order_id')
    new_status = request.POST.get('status')
    order_obj = get_object_or_404(Order, id=order_id, theater=theater)
    order_obj.status = new_status
    order_obj.save()
    return redirect('/shopkeeper/dashboard/?tab=live')

  if request.method == 'POST' and 'manage_menu' in request.POST:
    action = request.POST.get('action')
    item_id = request.POST.get('item_id')

    if action == 'delete' and item_id:
      FoodItem.objects.filter(id=item_id).delete()
    elif action in ['add', 'edit']:
      name = request.POST.get('name')
      category = request.POST.get('category')
      price = request.POST.get('price')
      qty = request.POST.get('quantity_available', 0)
      unit_spec = request.POST.get('unit_spec')  # Captured unit_spec
      image = request.FILES.get('image')

      if action == 'add':
        item = FoodItem.objects.create(
            theater=theater,
            name=name,
            category=category,
            price=price,
            quantity_available=qty,
            unit_spec=unit_spec,  # Saved unit_spec
        )
        if image:
          item.image = image
          item.save()
      elif action == 'edit' and item_id:
        item = get_object_or_404(FoodItem, id=item_id, theater=theater)
        item.name = name
        item.category = category
        item.price = price
        item.quantity_available = qty
        item.unit_spec = unit_spec  # Updated unit_spec
        if image:
          item.image = image
        item.save()
    return redirect('/shopkeeper/dashboard/?tab=menu')

  orders = (
      Order.objects.filter(theater=theater)
      .exclude(status='PENDING')
      .order_by('-created_at')
  )
  live_orders = orders.filter(status__in=['CONFIRMED', 'PREPARING', 'READY'])

  today_revenue = (
      orders.filter(status__in=['CONFIRMED', 'PREPARING', 'READY', 'DELIVERED'])
      .aggregate(total=Sum('total_amount'))['total']
      or 0
  )
  menu_items = (
      FoodItem.objects.filter(theater=theater)
      if hasattr(FoodItem, 'theater')
      else FoodItem.objects.all()
  )

  context = {
      'theater': theater,
      'active_tab': active_tab,
      'orders': orders,
      'live_orders': live_orders,
      'pending_count': live_orders.count(),
      'today_revenue': today_revenue,
      'menu_items': menu_items,
  }
  return render(request, 'shopkeeper_dashboard.html', context)


def order_detail_view(request, order_id):
  if not request.session.get('seat') or not request.session.get('theater_id'):
    return redirect('login')

  order = get_object_or_404(Order, id=order_id)
  order_items = OrderItem.objects.filter(order=order)

  return render(
      request,
      'order_detail.html',
      {
          'order': order,
          'order_items': order_items,
      },
  )