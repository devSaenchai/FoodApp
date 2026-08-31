import json
import urllib.parse
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.conf import settings
from .models import Theater, FoodItem, Order, OrderItem

def login_view(request):
    seat = request.GET.get('seat') or request.POST.get('seat') or request.session.get('seat', 'default')
    theater_id = request.GET.get('theater') or request.POST.get('theater') or request.session.get('theater_id', 1)
    
    if request.method == 'POST':
        request.session['customer_name'] = request.POST.get('name')
        request.session['customer_phone'] = request.POST.get('phone')
        request.session['seat'] = seat
        request.session['theater_id'] = theater_id
        return redirect('client_menu')
    
    if request.GET.get('seat'):
        request.session['seat'] = request.GET.get('seat')
    if request.GET.get('theater'):
        request.session['theater_id'] = request.GET.get('theater')

    return render(request, 'login.html', {'seat': seat})

def client_menu(request):
    theater_id = request.session.get('theater_id', 1)
    theater = Theater.objects.filter(id=theater_id).first() or Theater.objects.first()
    items = FoodItem.objects.filter(theater=theater, quantity_available__gt=0)
    
    return render(request, 'client_menu.html', {
        'items': items, 
        'theater': theater,
        'seat': request.session.get('seat', 'default')
    })

def checkout_view(request):
    return render(request, 'checkout.html', {
        'seat': request.session.get('seat', 'default')
    })

def create_direct_upi_order(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        items_data = data.get('items', [])
        payment_method = data.get('payment_method', 'ONLINE')
        fulfillment_type = data.get('fulfillment_type', 'SEAT')
        
        theater_id = request.session.get('theater_id', 1)
        theater = Theater.objects.filter(id=theater_id).first() or Theater.objects.first()
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

        # Build UPI Deep Link
        upi_id = getattr(settings, 'UPI_ID', 'sanjayn1229-6@okicici')
        merchant_name = getattr(settings, 'MERCHANT_NAME', 'Theater Snacks')
        note = f"Order #{order.id}"
        
        upi_url = (
            f"upi://pay?pa={upi_id}"
            f"&pn={urllib.parse.quote(merchant_name)}"
            f"&am={total_amount}"
            f"&cu=INR"
            f"&tn={urllib.parse.quote(note)}"
        )
        
        qr_api_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={urllib.parse.quote(upi_url)}"

        return JsonResponse({
            'status': 'UPI_GENERATED',
            'order_id': order.id,
            'total_amount': str(total_amount),
            'upi_deep_link': upi_url,
            'qr_image_url': qr_api_url
        })

def verify_upi_utr(request, order_id):
    if request.method == 'POST':
        order = get_object_or_404(Order, id=order_id)
        utr_number = request.POST.get('utr_number')
        order.transaction_id = utr_number
        order.status = 'CONFIRMED'
        order.save()
        return redirect('order_confirmed', order_id=order.id)

def order_confirmed(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    return render(request, 'order_confirmed.html', {'order': order})