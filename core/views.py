import json
import razorpay
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from .models import Theater, FoodItem, Order, OrderItem

def login_view(request):
    seat = request.GET.get('seat') or request.POST.get('seat')
    theater_id = request.GET.get('theater') or request.POST.get('theater')

    if seat and theater_id:
        request.session['seat'] = seat
        request.session['theater_id'] = theater_id

    # Enforce QR code scan requirement
    if not request.session.get('seat') or not request.session.get('theater_id'):
        return HttpResponseForbidden("Access Denied: You must scan a valid theater QR code to access this ordering portal.")

    theater = get_object_or_404(Theater, id=request.session.get('theater_id'))

    if request.method == 'POST':
        name = request.POST.get('name')
        phone = request.POST.get('phone')
        if name and phone:
            request.session['customer_name'] = name
            request.session['customer_phone'] = phone
            return redirect('client_menu')

    return render(request, 'login.html', {'theater': theater, 'seat': request.session.get('seat')})

def client_menu(request):
    if not request.session.get('seat') or not request.session.get('theater_id'):
        return redirect('login')
    
    theater_id = request.session.get('theater_id')
    theater = get_object_or_404(Theater, id=theater_id)
    
    category = request.GET.get('category', 'ALL')
    search_query = request.GET.get('q', '')

    items = FoodItem.objects.filter(theater=theater, quantity_available__gt=0)
    if category != 'ALL':
        items = items.filter(category=category)
    if search_query:
        items = items.filter(Q(name__icontains=search_query) | Q(unit_spec__icontains=search_query))

    return render(request, 'client_menu.html', {
        'items': items, 
        'theater': theater,
        'seat': request.session.get('seat'),
        'customer_name': request.session.get('customer_name'),
        'current_category': category,
        'search_query': search_query
    })

def my_orders_view(request):
    if not request.session.get('seat') or not request.session.get('theater_id'):
        return redirect('login')
    
    customer_phone = request.session.get('customer_phone')
    order_history = Order.objects.filter(customer_phone=customer_phone).order_by('-created_at') if customer_phone else []

    return render(request, 'my_orders.html', {
        'order_history': order_history,
        'customer_phone': customer_phone
    })

def checkout_view(request):
    if not request.session.get('seat') or not request.session.get('theater_id'):
        return redirect('login')
    theater = Theater.objects.filter(id=request.session.get('theater_id')).first()
    return render(request, 'checkout.html', {
        'seat': request.session.get('seat', 'default'),
        'theater_name': theater.name if theater else 'Theater'
    })

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
            order_items_to_create = []

            for item in items_data:
                food_item = get_object_or_404(FoodItem, id=item['id'])
                qty = int(item['qty'])
                subtotal += food_item.price * qty
                order_items_to_create.append((food_item, qty, food_item.price))

            delivery_fee = 10 if fulfillment_type == 'SEAT' else 0
            total_amount = subtotal + delivery_fee
            amount_in_paise = int(total_amount * 100)

            if amount_in_paise < 100:
                return JsonResponse({'error': 'Minimum order amount must be at least ₹1.'}, status=400)

            order = Order.objects.create(
                theater=theater,
                customer_name=customer_name,
                customer_phone=customer_phone,
                seat_number=seat_number,
                fulfillment_type=fulfillment_type,
                payment_method=payment_method,
                total_amount=total_amount,
                status='CONFIRMED' if payment_method == 'CASH' else 'PENDING'
            )

            for food_item, qty, price in order_items_to_create:
                OrderItem.objects.create(order=order, food_item=food_item, quantity=qty, price=price)

            if payment_method == 'CASH':
                return JsonResponse({'status': 'CASH_SUCCESS', 'order_id': order.id})

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            razorpay_order = client.order.create({
                "amount": amount_in_paise,
                "currency": "INR",
                "receipt": f"order_rcptid_{order.id}",
                "payment_capture": 1
            })
            
            order.razorpay_order_id = razorpay_order['id']
            order.save()

            return JsonResponse({
                'status': 'RAZORPAY_ORDER_CREATED',
                'order_id': order.id,
                'razorpay_order_id': razorpay_order['id'],
                'razorpay_merchant_key': settings.RAZORPAY_KEY_ID,
                'total_amount': amount_in_paise,
                'currency': 'INR'
            })
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def verify_razorpay_payment(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            razorpay_order_id = data.get('razorpay_order_id')
            razorpay_payment_id = data.get('razorpay_payment_id')
            razorpay_signature = data.get('razorpay_signature')
            order_id = data.get('order_id')

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            })

            order = get_object_or_404(Order, id=order_id)
            order.transaction_id = razorpay_payment_id
            order.status = 'CONFIRMED'
            order.is_paid = True
            order.save()

            return JsonResponse({'status': 'SUCCESS', 'order_id': order.id})
        except Exception as e:
            return JsonResponse({'status': 'FAILURE', 'error': str(e)}, status=400)
    return HttpResponseBadRequest("Invalid request method")

def order_confirmed(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    return render(request, 'order_confirmed.html', {'order': order})