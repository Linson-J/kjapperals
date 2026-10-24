"""
Setup script for Kjapperals customer.
Run with: $env:ENV_FILE=".env.kjapperals"; python setup_kjapperals.py
"""
import os
import sys
import django
import urllib.request
import pathlib

# ── Bootstrap Django ──────────────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')
django.setup()

from django.core.files import File
from shop.models import StoreAddressSettings, CarouselItem

MEDIA_ROOT = pathlib.Path(__file__).parent / 'media'

# ── Image sources (Unsplash – free, no auth needed) ───────────────────────────
LOGO_URL    = 'https://images.unsplash.com/photo-1602752250015-52934bc45613?w=400&q=80'
SLIDER_IMAGES = [
    {
        'url': 'https://images.unsplash.com/photo-1515562141207-7a88fb7ce338?w=1400&q=80',
        'filename': 'kjapperals_slider_1.jpg',
        'title': 'Elegant Jewellery Collection',
        'description': 'Discover our handcrafted jewellery for every occasion.',
        'button_text': 'Shop Now',
    },
    {
        'url': 'https://images.unsplash.com/photo-1601821765780-754fa98637c1?w=1400&q=80',
        'filename': 'kjapperals_slider_2.jpg',
        'title': 'Premium Gold & Silver',
        'description': 'Timeless pieces crafted with love in Tirunelveli.',
        'button_text': 'Explore',
    },
    {
        'url': 'https://images.unsplash.com/photo-1535632066927-ab7c9ab60908?w=1400&q=80',
        'filename': 'kjapperals_slider_3.jpg',
        'title': 'New Arrivals',
        'description': 'Fresh styles inspired by South Indian tradition.',
        'button_text': 'View Collection',
    },
]

def download(url, dest_path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        print(f'  already exists: {dest_path.name}')
        return
    print(f'  downloading → {dest_path.name} ...')
    headers = {'User-Agent': 'Mozilla/5.0'}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp, open(dest_path, 'wb') as f:
        f.write(resp.read())
    print(f'  saved: {dest_path.name}')


# ─────────────────────────────────────────────────────────────────────────────
# 1. Download logo
# ─────────────────────────────────────────────────────────────────────────────
print('\n[1] Downloading logo...')
logo_path = MEDIA_ROOT / 'store' / 'logos' / 'kjapperals_logo.jpg'
download(LOGO_URL, logo_path)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Store settings
# ─────────────────────────────────────────────────────────────────────────────
print('\n[2] Setting up store info...')
store, created = StoreAddressSettings.objects.get_or_create(pk=1)
store.sender_name        = 'Kjapperals'
store.sender_phone       = '+91 98765 43210'
store.contact_phone      = '+91 98765 43210'
store.whatsapp_number    = '+91 98765 43210'
store.contact_email      = 'hello@kjapperals.com'
store.sender_address     = 'No. 12, Main Bazaar Street, Palayamkottai, Tirunelveli - 627002, Tamil Nadu'
store.business_hours     = 'Mon – Sat: 9:00 AM – 8:00 PM'
store.announcement_enabled    = True
store.announcement_message_1  = 'Free delivery on orders above ₹999 across Tirunelveli!'
store.announcement_message_2  = 'Handcrafted jewellery from the heart of Tirunelveli.'
store.announcement_message_3  = 'New Collection Arrived — Explore Now!'
store.cod_enabled        = True

# attach logo
with open(logo_path, 'rb') as f:
    store.brand_logo.save('kjapperals_logo.jpg', File(f), save=False)

store.save()
print(f'  Store {"created" if created else "updated"}: {store.sender_name}')
print(f'  Address: {store.sender_address}')
print(f'  Logo: {store.brand_logo.name}')


# ─────────────────────────────────────────────────────────────────────────────
# 3. Home slider (CarouselItems)
# ─────────────────────────────────────────────────────────────────────────────
print('\n[3] Setting up home slider...')

# Clear old carousel items first
deleted, _ = CarouselItem.objects.all().delete()
if deleted:
    print(f'  Cleared {deleted} old slider item(s)')

for i, slide in enumerate(SLIDER_IMAGES, start=1):
    img_path = MEDIA_ROOT / 'carousel' / slide['filename']
    download(slide['url'], img_path)

    item = CarouselItem(
        title        = slide['title'],
        description  = slide['description'],
        button_text  = slide['button_text'],
        redirect_type= 'page',
        customer_page= '/#collection',
        is_active    = True,
        order        = i,
    )
    with open(img_path, 'rb') as f:
        item.image.save(slide['filename'], File(f), save=False)
    item.save()
    print(f'  Slide {i} added: {slide["title"]}')


# ─────────────────────────────────────────────────────────────────────────────
print('\n✓ Kjapperals setup complete!\n')
