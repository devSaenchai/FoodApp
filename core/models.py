from django.db import models


class Theater(models.Model):
  name = models.CharField(max_length=100)
  location = models.CharField(max_length=200, blank=True, null=True)
  operating_hours = models.CharField(
      max_length=100, default="10:00 AM - 11:30 PM"
  )
  phone_number = models.CharField(max_length=20, default="0484 123 4567")

  def __str__(self):
    return self.name


class FoodItem(models.Model):
  CATEGORY_CHOICES = [
      ("ALL", "All Items"),
      ("DRINKS", "Drinks"),
      ("SNACKS", "Snacks"),
      ("COMBO", "Combos"),
  ]
  theater = models.ForeignKey(
      Theater, on_delete=models.CASCADE, related_name="food_items"
  )
  name = models.CharField(max_length=100)
  category = models.CharField(
      max_length=20, choices=CATEGORY_CHOICES, default="SNACKS"
  )
  price = models.DecimalField(max_digits=8, decimal_places=2)
  quantity_available = models.PositiveIntegerField(default=0)
  unit_spec = models.CharField(max_length=50, blank=True, null=True)
  image = models.ImageField(upload_to="food_images/", blank=True, null=True)

  def __str__(self):
    return f"{self.name} ({self.theater.name})"


class Order(models.Model):
  FULFILLMENT_CHOICES = [
      ("SEAT", "Deliver to Seat"),
      ("COUNTER", "Counter Pickup"),
  ]
  STATUS_CHOICES = [
      ("PENDING", "Pending Payment Verification"),
      ("CONFIRMED", "Confirmed / Preparing"),
      ("COMPLETED", "Completed"),
      ("CANCELLED", "Cancelled"),
  ]

  theater = models.ForeignKey(Theater, on_delete=models.CASCADE)
  customer_name = models.CharField(max_length=100)
  customer_phone = models.CharField(max_length=15)
  seat_number = models.CharField(max_length=20, default="Counter")
  fulfillment_type = models.CharField(
      max_length=10, choices=FULFILLMENT_CHOICES, default="SEAT"
  )
  payment_method = models.CharField(max_length=20, default="ONLINE")
  total_amount = models.DecimalField(max_digits=10, decimal_places=2)
  razorpay_order_id = models.CharField(max_length=100, blank=True, null=True)
  transaction_id = models.CharField(max_length=50, blank=True, null=True)
  status = models.CharField(
      max_length=20, choices=STATUS_CHOICES, default="PENDING"
  )
  is_paid = models.BooleanField(default=False)
  created_at = models.DateTimeField(auto_now_add=True)

  def __str__(self):
    return f"Order #{self.id} - {self.customer_name} ({self.customer_phone})"


class OrderItem(models.Model):
  order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
  food_item = models.ForeignKey(FoodItem, on_delete=models.CASCADE)
  quantity = models.PositiveIntegerField(default=1)
  price = models.DecimalField(max_digits=8, decimal_places=2)

  def __str__(self):
    return f"{self.quantity}x {self.food_item.name}"