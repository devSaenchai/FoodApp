import json
import razorpay
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.db.models import Q
from .models import Theater, FoodItem, Order, OrderItem
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login
from django.db.models import Sum, Count
from django.db import transaction


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

    # Show items even if out of stock, so we can display "Out of Stock" labels to users
    items = FoodItem.objects.filter(theater=theater)
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
            order_items_to_create = []

            # First pass: Validate stock availability and lock items
            for item in items_data:
                food_item = get_object_or_404(FoodItem, id=item['id'])
                qty = int(item['qty'])
                
                if food_item.quantity_available < qty:
                    return JsonResponse({'error': f'Sorry, "{food_item.name}" only has {food_item.quantity_available} left in stock.'}, status=400)
                
                subtotal += food_item.price * qty
                order_items_to_create.append((food_item, qty, food_item.price))

            delivery_fee = 10 if fulfillment_type == 'SEAT' else 0
            total_amount = subtotal + delivery_fee
            amount_in_paise = int(total_amount * 100)

            if amount_in_paise < 100:
                return JsonResponse({'error': 'Minimum order amount must be at least ₹1.'}, status=400)

            # Create Order
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

            # Create OrderItems and auto-decrement stock
            for food_item, qty, price in order_items_to_create:
                OrderItem.objects.create(order=order, food_item=food_item, quantity=qty, price=price)
                food_item.quantity_available -= qty
                if food_item.quantity_available < 0:
                    food_item.quantity_available = 0
                food_item.save()

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

def shopkeeper_login_view(request, theater_id):
    theater = get_object_or_404(Theater, id=theater_id)
    error = None
    
    if request.method == "POST":
        username = request.POST.get('username')
        password = request.POST.get('password')
        
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('shopkeeper_dashboard')  
        else:
            error = "Invalid username or password."
            
    return render(request, 'shopkeeper_login.html', {'theater': theater, 'error': error})

@login_required
def shopkeeper_dashboard_view(request):
    theater = Theater.objects.first()
    active_tab = request.GET.get('tab', 'live')
    
    # Handle Order Status Update
    if request.method == 'POST' and 'update_order_status' in request.POST:
        order_id = request.POST.get('order_id')
        new_status = request.POST.get('status')
        order_obj = get_object_or_404(Order, id=order_id, theater=theater)
        order_obj.status = new_status
        order_obj.save()
        return redirect('/shopkeeper/dashboard/?tab=live')

    # Handle Menu Item Actions (Add/Edit/Delete) with Image Support
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
            image = request.FILES.get('image')
            
            if action == 'add':
                item = FoodItem.objects.create(
                    theater=theater, 
                    name=name, 
                    category=category, 
                    price=price, 
                    quantity_available=qty
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
                if image:
                    item.image = image
                item.save()
        return redirect('/shopkeeper/dashboard/?tab=menu')

    orders = Order.objects.filter(theater=theater).order_by('-created_at')
    live_orders = orders.exclude(status='DELIVERED') 
    
    today_revenue = orders.aggregate(total=Sum('total_amount'))['total'] or 0
    menu_items = FoodItem.objects.filter(theater=theater) if hasattr(FoodItem, 'theater') else FoodItem.objects.all()

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

    return render(request, 'order_detail.html', {
        'order': order,
        'order_items': order_items,
    })