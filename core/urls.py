from django.urls import path
from . import views

urlpatterns = [
    path('', views.login_view, name='login'),
    path('menu/', views.client_menu, name='client_menu'),
    path('my-orders/', views.my_orders_view, name='my_orders'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('create-direct-upi-order/', views.create_direct_upi_order, name='create_direct_upi_order'),
    path('verify-payment/', views.verify_razorpay_payment, name='verify_razorpay_payment'),
    path('order-confirmed/<int:order_id>/', views.order_confirmed, name='order_confirmed'),
]