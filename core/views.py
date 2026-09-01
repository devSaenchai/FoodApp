import json
import razorpay
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseBadRequest
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
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
        try:
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
            amount_in_paise = int(total_amount * 100)

            # Minimum amount check (100 paise = ₹1)
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

            # Initialize Razorpay Client
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            
            razorpay_data = {
                "amount": amount_in_paise,
                "currency": "INR",
                "receipt": f"order_rcptid_{order.id}",
                "payment_capture": 1
            }
            
            razorpay_order = client.order.create(data=razorpay_data)
            
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

            if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature, order_id]):
                return JsonResponse({'status': 'FAILURE', 'error': 'Missing payment fields'}, status=400)

            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
            
            # Verify signature using Razorpay utility
            params_dict = {
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            }
            
            client.utility.verify_payment_signature(params_dict)

            # If signature verification succeeds, update order status
            order = get_object_or_404(Order, id=order_id)
            order.transaction_id = razorpay_payment_id
            order.status = 'CONFIRMED'
            order.is_paid = True
            order.save()

            return JsonResponse({'status': 'SUCCESS', 'order_id': order.id})
        
        except razorpay.errors.SignatureVerificationError:
            return JsonResponse({'status': 'FAILURE', 'error': 'Signature verification failed'}, status=400)
        except Exception as e:
            return JsonResponse({'status': 'FAILURE', 'error': str(e)}, status=500)
            
    return HttpResponseBadRequest("Invalid request method")

def verify_upi_utr(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    if request.method == 'POST':
        order.status = 'CONFIRMED'
        order.is_paid = True
        order.save()
        return JsonResponse({'status': 'SUCCESS', 'order_id': order.id})
    return JsonResponse({'status': 'FAILURE', 'error': 'Invalid request method'}, status=400)

def order_confirmed(request, order_id):
    order = get_object_or_404(Order, id=order_id)
    return render(request, 'order_confirmed.html', {'order': order})