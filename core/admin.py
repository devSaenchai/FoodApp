from django.contrib import admin
from .models import Theater, FoodItem, Order, OrderItem

@admin.register(Theater)
class TheaterAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'location', 'phone_number')
    
    def get_urls(self):
        urls = super().get_urls()
        from django.urls import path
        custom_urls = [
            path('<int:theater_id>/shopkeeper-login-link/', self.admin_site.admin_view(self.shopkeeper_link_view), name='theater_shopkeeper_link'),
        ]
        return custom_urls + urls

    def shopkeeper_link_view(self, request, theater_id):
        from django.shortcuts import redirect
        return redirect('shopkeeper_login', theater_id=theater_id)

@admin.register(FoodItem)
class FoodItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'name', 'theater', 'price', 'quantity_available', 'unit_spec')
    list_filter = ('theater',)
    search_fields = ('name',)

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('food_item', 'quantity', 'price')

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id', 'customer_name', 'customer_phone', 'seat_number', 'fulfillment_type', 'payment_method', 'total_amount', 'status', 'created_at')
    list_filter = ('status', 'fulfillment_type', 'payment_method', 'created_at')
    search_fields = ('customer_name', 'customer_phone', 'transaction_id')
    inlines = [OrderItemInline]

