import os
import qrcode

# 1. Set your server domain or local IP (e.g., "http://192.168.1.50:8000")
BASE_URL = "http://192.168.1.50:8000"

# 2. Define the Theater/Screen ID you are generating codes for
THEATER_ID = 1

# 3. Define seat list layout
ROWS = ['A', 'B', 'C']
SEATS_PER_ROW = 5

# Directory to save generated QR code images
OUTPUT_DIR = "generated_qr_codes"
os.makedirs(OUTPUT_DIR, exist_ok=True)

def generate_seat_qrs():
    generated_count = 0

    for row in ROWS:
        for seat_num in range(1, SEATS_PER_ROW + 1):
            seat_code = f"{row}{seat_num}"
            
            # Construct destination URL passing both theater ID and seat code
            target_url = f"{BASE_URL}/?theater={THEATER_ID}&seat={seat_code}"
            
            # Configure QR Code configuration
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_M,
                box_size=10,
                border=4,
            )
            qr.add_data(target_url)
            qr.make(fit=True)

            # Generate high-contrast black and white image
            img = qr.make_image(fill_color="black", back_color="white")
            
            # Save file as PNG
            filename = f"theater_{THEATER_ID}_seat_{seat_code}.png"
            filepath = os.path.join(OUTPUT_DIR, filename)
            img.save(filepath)
            
            generated_count += 1
            print(f"Generated QR: Seat {seat_code} (Theater {THEATER_ID}) -> {filepath}")

    print(f"\nDone! Generated {generated_count} QR codes in '{OUTPUT_DIR}/'.")

if __name__ == "__main__":
    generate_seat_qrs()