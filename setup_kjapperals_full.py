"""
Full setup for Kjapperals Garments Store – Tirunelveli
Run: $env:ENV_FILE=".env.kjapperals"; python setup_kjapperals_full.py
"""
import os, sys, django, urllib.request, pathlib, time

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')
django.setup()

from django.core.files import File
from shop.models import (
    StoreAddressSettings, CarouselItem, Category,
    Product, ProductImage, HomeNewsTicker, HomeTheme, GSTRate,
)

MEDIA = pathlib.Path(__file__).parent / 'media'

# ─── helpers ─────────────────────────────────────────────────────────────────
def dl(url, path, fallback=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return True
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=20) as r, open(path, 'wb') as f:
            f.write(r.read())
        print(f'    ↓ {path.name}')
        return True
    except Exception as e:
        print(f'    ✗ {path.name} ({e})')
        if fallback:
            return dl(fallback, path)
        return False

def attach(obj, field_name, path):
    if path.exists():
        with open(path, 'rb') as f:
            getattr(obj, field_name).save(path.name, File(f), save=False)

# ─── 1. Store Settings ───────────────────────────────────────────────────────
print('\n[1] Store Settings')
store, _ = StoreAddressSettings.objects.get_or_create(pk=1)
store.sender_name           = 'Kjapperals'
store.sender_phone          = '+91 94876 12345'
store.contact_phone         = '+91 94876 12345'
store.whatsapp_number       = '+91 94876 12345'
store.contact_email         = 'hello@kjapperals.com'
store.sender_address        = 'No. 45, Junction Road, Palayamkottai, Tirunelveli – 627 002, Tamil Nadu'
store.business_hours        = 'Mon – Sat: 9:00 AM – 9:00 PM  |  Sun: 10:00 AM – 6:00 PM'
store.announcement_enabled  = True
store.announcement_message_1 = '🎉 Mega Sale — Up to 50% Off on All Ethnic Wear!'
store.announcement_message_2 = '🚚 Free Delivery on Orders Above ₹999 Across Tirunelveli!'
store.announcement_message_3 = '✨ New Collection Arrived — Shop the Latest Trends Now!'
store.cod_enabled           = True
store.save()
print(f'  ✓ {store.sender_name} — {store.sender_address}')

# ─── 2. Home Theme (warm ethnic palette) ─────────────────────────────────────
print('\n[2] Home Theme')
theme, _ = HomeTheme.objects.get_or_create(pk=1)
theme.primary_color          = '#8B1A4A'   # deep rose / maroon
theme.background_color       = '#FFF9F5'   # warm off-white
theme.dark_background_color  = '#1A0A0E'
theme.sale_background_color  = '#FFD6E0'
theme.text_color             = '#1A0A0E'
theme.muted_text_color       = '#6B4455'
theme.surface_color          = '#FFFFFF'
theme.save()
print('  ✓ Ethnic warm palette applied')

# ─── 3. Home Slider ──────────────────────────────────────────────────────────
print('\n[3] Home Slider')
CarouselItem.objects.all().delete()

SLIDES = [
    {
        'img': 'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=1400&q=80',
        'fb':  'https://images.unsplash.com/photo-1469334031218-e382a71b716b?w=1400&q=80',
        'file': 'slide_ethnic.jpg',
        'title': 'Ethnic Wear Collection',
        'desc':  'Celebrate every occasion with our handpicked ethnic collection.',
        'btn':   'Shop Ethnic',
    },
    {
        'img': 'https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=1400&q=80',
        'fb':  'https://images.unsplash.com/photo-1496747611176-843222e1e57c?w=1400&q=80',
        'file': 'slide_women.jpg',
        'title': "Women's New Arrivals",
        'desc':  'Fresh styles for every mood — kurtas, gowns, sarees & more.',
        'btn':   'Explore Now',
    },
    {
        'img': 'https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=1400&q=80',
        'fb':  'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=1400&q=80',
        'file': 'slide_men.jpg',
        'title': "Men's Premium Wear",
        'desc':  'Sharp, comfortable & affordable — shirts, kurtas, formals.',
        'btn':   'Shop Men',
    },
]

for i, s in enumerate(SLIDES, 1):
    p = MEDIA / 'carousel' / s['file']
    dl(s['img'], p, s['fb'])
    item = CarouselItem(
        title=s['title'], description=s['desc'],
        button_text=s['btn'], redirect_type='page',
        customer_page='/#collection', is_active=True, order=i,
    )
    attach(item, 'image', p)
    item.save()
    print(f'  ✓ Slide {i}: {s["title"]}')

# ─── 4. News Ticker ───────────────────────────────────────────────────────────
print('\n[4] News Ticker')
HomeNewsTicker.objects.all().delete()
tickers = [
    ('🔥 Mega Sale — Up to 50% Off on Ethnic Wear!', 1),
    ('👗 New Women\'s Collection Just Arrived — Shop Now!', 2),
    ('👔 Men\'s Formal & Casual Wear Starting ₹399!', 3),
    ('🧒 Kids\' Party Wear — Adorable Styles for Every Occasion!', 4),
    ('🚚 Free Delivery on Orders Above ₹999 in Tirunelveli!', 5),
    ('⭐ Premium Quality Fabrics — Cotton, Silk & More!', 6),
    ('📍 Visit Us: Junction Road, Palayamkottai, Tirunelveli', 7),
]
for msg, order in tickers:
    HomeNewsTicker.objects.create(message=msg, display_order=order, is_active=True)
    print(f'  ✓ {msg[:55]}...')

# ─── 5. Categories ───────────────────────────────────────────────────────────
print('\n[5] Categories')
CAT_DATA = [
    {
        'name': "Women's Ethnic Wear", 'desc': 'Sarees, kurtis, churidars, lehengas and more for women.',
        'img_url': 'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'img_fb':  'https://images.unsplash.com/photo-1469334031218-e382a71b716b?w=600&q=80',
        'file': 'cat_women_ethnic.jpg', 'popular': True,
    },
    {
        'name': "Women's Western Wear", 'desc': 'Tops, dresses, gowns and trendy western outfits.',
        'img_url': 'https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600&q=80',
        'img_fb':  'https://images.unsplash.com/photo-1496747611176-843222e1e57c?w=600&q=80',
        'file': 'cat_women_western.jpg', 'popular': True,
    },
    {
        'name': "Men's Formals", 'desc': 'Formal shirts, trousers and office wear for men.',
        'img_url': 'https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=600&q=80',
        'img_fb':  'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=600&q=80',
        'file': 'cat_men_formal.jpg', 'popular': True,
    },
    {
        'name': "Men's Casual Wear", 'desc': 'T-shirts, jeans, casual shirts and kurtas for men.',
        'img_url': 'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=600&q=80',
        'img_fb':  'https://images.unsplash.com/photo-1542272604-787c3835535d?w=600&q=80',
        'file': 'cat_men_casual.jpg', 'popular': True,
    },
    {
        'name': "Kids' Wear", 'desc': 'Cute and comfortable dresses for boys and girls.',
        'img_url': 'https://images.unsplash.com/photo-1518831959646-742c3a14ebf7?w=600&q=80',
        'img_fb':  'https://images.unsplash.com/photo-1471286174890-9c112ac6476c?w=600&q=80',
        'file': 'cat_kids.jpg', 'popular': True,
    },
    {
        'name': 'Sarees', 'desc': 'Cotton, silk, chiffon and designer sarees.',
        'img_url': 'https://images.unsplash.com/photo-1583391733956-3750e0ff4e8b?w=600&q=80',
        'img_fb':  'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'file': 'cat_sarees.jpg', 'popular': True,
    },
]

cat_map = {}
for c in CAT_DATA:
    p = MEDIA / 'categories' / c['file']
    dl(c['img_url'], p, c['img_fb'])
    obj, created = Category.objects.get_or_create(
        name=c['name'],
        defaults={'description': c['desc'], 'status': 'active', 'is_popular': c['popular']},
    )
    if p.exists():
        with open(p, 'rb') as f:
            obj.image.save(c['file'], File(f), save=False)
    obj.is_popular = c['popular']
    obj.save()
    cat_map[c['name']] = obj
    print(f'  ✓ {c["name"]}')

# ─── 6. Products ─────────────────────────────────────────────────────────────
print('\n[6] Products (22 items)')

PRODUCTS = [
    # ── Women's Ethnic ──────────────────────────────────────────────────────
    {
        'name': 'Floral Printed Kurti', 'cat': "Women's Ethnic Wear",
        'price': 899, 'disc': 649, 'stock': 50,
        'colors': ['Red', 'Blue', 'Green'],
        'desc': 'Breathable cotton kurti with vibrant floral print. Perfect for daily wear and casual outings.',
        'img': 'https://images.unsplash.com/photo-1594938298603-c8148c4b4d8e?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1469334031218-e382a71b716b?w=600&q=80',
        'file': 'prod_kurti_floral.jpg',
    },
    {
        'name': 'Anarkali Churidar Set', 'cat': "Women's Ethnic Wear",
        'price': 1499, 'disc': 1099, 'stock': 30,
        'colors': ['Maroon', 'Navy', 'Teal'],
        'desc': 'Elegant Anarkali suit with embroidered neckline. Ideal for festivals and family functions.',
        'img': 'https://images.unsplash.com/photo-1571844307880-751c6a4b8e1d?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'file': 'prod_anarkali.jpg',
    },
    {
        'name': 'Cotton Churidar Suit', 'cat': "Women's Ethnic Wear",
        'price': 1199, 'disc': 899, 'stock': 40,
        'colors': ['Pink', 'Yellow', 'White'],
        'desc': 'Comfortable and stylish cotton churidar with dupatta. Suitable for office and casual wear.',
        'img': 'https://images.unsplash.com/photo-1529139574466-a303027c1d8b?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1469334031218-e382a71b716b?w=600&q=80',
        'file': 'prod_churidar.jpg',
    },
    {
        'name': 'Embroidered Lehenga Choli', 'cat': "Women's Ethnic Wear",
        'price': 2999, 'disc': 2199, 'stock': 20,
        'colors': ['Red', 'Pink', 'Golden'],
        'desc': 'Stunning lehenga choli with intricate embroidery work. Perfect for weddings and special occasions.',
        'img': 'https://images.unsplash.com/photo-1617627143750-d86bc21e42bb?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'file': 'prod_lehenga.jpg',
    },
    # ── Sarees ──────────────────────────────────────────────────────────────
    {
        'name': 'Kanjivaram Silk Saree', 'cat': 'Sarees',
        'price': 4500, 'disc': 3799, 'stock': 15,
        'colors': ['Maroon', 'Blue', 'Green'],
        'desc': 'Traditional Kanjivaram silk saree with golden zari border. A timeless choice for weddings.',
        'img': 'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1583391733956-3750e0ff4e8b?w=600&q=80',
        'file': 'prod_silk_saree.jpg',
    },
    {
        'name': 'Printed Cotton Saree', 'cat': 'Sarees',
        'price': 799, 'disc': 599, 'stock': 60,
        'colors': ['Yellow', 'Pink', 'Orange'],
        'desc': 'Lightweight printed cotton saree perfect for daily use and office wear.',
        'img': 'https://images.unsplash.com/photo-1583391733956-3750e0ff4e8b?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'file': 'prod_cotton_saree.jpg',
    },
    {
        'name': 'Chiffon Designer Saree', 'cat': 'Sarees',
        'price': 1899, 'disc': 1399, 'stock': 25,
        'colors': ['Peach', 'Lavender', 'Mint'],
        'desc': 'Soft chiffon saree with designer blouse. Elegant choice for parties and events.',
        'img': 'https://images.unsplash.com/photo-1504439468489-c8920d796a29?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1610030469983-98e550d6193c?w=600&q=80',
        'file': 'prod_chiffon_saree.jpg',
    },
    # ── Women's Western ──────────────────────────────────────────────────────
    {
        'name': 'Floral Maxi Dress', 'cat': "Women's Western Wear",
        'price': 1299, 'disc': 949, 'stock': 35,
        'colors': ['Blue', 'Pink', 'White'],
        'desc': 'Breezy floral maxi dress made from lightweight fabric. Great for casual outings.',
        'img': 'https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1496747611176-843222e1e57c?w=600&q=80',
        'file': 'prod_maxi_dress.jpg',
    },
    {
        'name': 'Party Wear Gown', 'cat': "Women's Western Wear",
        'price': 1999, 'disc': 1499, 'stock': 20,
        'colors': ['Black', 'Navy', 'Wine'],
        'desc': 'Stylish evening gown for parties and formal events. Flattering A-line silhouette.',
        'img': 'https://images.unsplash.com/photo-1595777457583-95e059d581b8?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1515372039744-b8f02a3ae446?w=600&q=80',
        'file': 'prod_gown.jpg',
    },
    {
        'name': "Women's Crop Top", 'cat': "Women's Western Wear",
        'price': 599, 'disc': 449, 'stock': 70,
        'colors': ['White', 'Black', 'Yellow'],
        'desc': 'Casual cotton crop top for everyday wear. Pair with jeans or skirts.',
        'img': 'https://images.unsplash.com/photo-1434389677669-e08b4cac3105?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1496747611176-843222e1e57c?w=600&q=80',
        'file': 'prod_crop_top.jpg',
    },
    # ── Men's Formals ────────────────────────────────────────────────────────
    {
        'name': 'Classic Formal Shirt', 'cat': "Men's Formals",
        'price': 799, 'disc': 599, 'stock': 80,
        'colors': ['White', 'Light Blue', 'Grey'],
        'desc': 'Premium cotton formal shirt with slim fit. A wardrobe essential for professionals.',
        'img': 'https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=600&q=80',
        'file': 'prod_formal_shirt.jpg',
    },
    {
        'name': 'Formal Trousers', 'cat': "Men's Formals",
        'price': 999, 'disc': 749, 'stock': 60,
        'colors': ['Black', 'Navy', 'Charcoal'],
        'desc': 'Well-tailored formal trousers with a modern slim fit. Ideal for office and formal events.',
        'img': 'https://images.unsplash.com/photo-1506629082955-511b1aa562c8?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=600&q=80',
        'file': 'prod_formal_trousers.jpg',
    },
    {
        'name': 'Blazer & Trouser Set', 'cat': "Men's Formals",
        'price': 2499, 'disc': 1899, 'stock': 15,
        'colors': ['Navy', 'Charcoal', 'Black'],
        'desc': 'Smart blazer and trouser co-ord set for a polished look. Perfect for meetings and events.',
        'img': 'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1602810318383-e386cc2a3ccf?w=600&q=80',
        'file': 'prod_blazer_set.jpg',
    },
    # ── Men's Casual ─────────────────────────────────────────────────────────
    {
        'name': "Men's Cotton Kurta", 'cat': "Men's Casual Wear",
        'price': 699, 'disc': 529, 'stock': 55,
        'colors': ['White', 'Cream', 'Sky Blue'],
        'desc': 'Soft cotton kurta with traditional print. Comfortable for festivals and casual occasions.',
        'img': 'https://images.unsplash.com/photo-1503341504253-dff4815485f1?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=600&q=80',
        'file': 'prod_men_kurta.jpg',
    },
    {
        'name': 'Casual Round Neck T-Shirt', 'cat': "Men's Casual Wear",
        'price': 399, 'disc': 299, 'stock': 120,
        'colors': ['Black', 'White', 'Navy', 'Red'],
        'desc': 'Everyday comfortable round neck t-shirt in pure cotton. Available in multiple colours.',
        'img': 'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1542272604-787c3835535d?w=600&q=80',
        'file': 'prod_tshirt.jpg',
    },
    {
        'name': 'Slim Fit Denim Jeans', 'cat': "Men's Casual Wear",
        'price': 1299, 'disc': 999, 'stock': 45,
        'colors': ['Dark Blue', 'Light Blue', 'Black'],
        'desc': 'Classic slim fit denim jeans with stretch fabric for all-day comfort.',
        'img': 'https://images.unsplash.com/photo-1542272604-787c3835535d?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=600&q=80',
        'file': 'prod_jeans.jpg',
    },
    {
        'name': 'Casual Check Shirt', 'cat': "Men's Casual Wear",
        'price': 699, 'disc': 499, 'stock': 65,
        'colors': ['Blue/White', 'Red/Black', 'Green/White'],
        'desc': 'Trendy check pattern shirt for casual outings. Relaxed fit with comfortable fabric.',
        'img': 'https://images.unsplash.com/photo-1473966968600-fa801b869a1a?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1521572163474-6864f9cf17ab?w=600&q=80',
        'file': 'prod_check_shirt.jpg',
    },
    # ── Kids' Wear ───────────────────────────────────────────────────────────
    {
        'name': "Girls' Party Frock", 'cat': "Kids' Wear",
        'price': 799, 'disc': 599, 'stock': 40,
        'colors': ['Pink', 'Purple', 'Yellow'],
        'desc': 'Adorable party frock for girls with net layers and bow detail. Perfect for birthdays.',
        'img': 'https://images.unsplash.com/photo-1518831959646-742c3a14ebf7?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1471286174890-9c112ac6476c?w=600&q=80',
        'file': 'prod_girls_frock.jpg',
    },
    {
        'name': "Boys' Cotton Shirt & Pant Set", 'cat': "Kids' Wear",
        'price': 699, 'disc': 499, 'stock': 50,
        'colors': ['White/Blue', 'White/Black'],
        'desc': 'Smart casual shirt and pant combo for boys. Ideal for school events and family functions.',
        'img': 'https://images.unsplash.com/photo-1471286174890-9c112ac6476c?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1518831959646-742c3a14ebf7?w=600&q=80',
        'file': 'prod_boys_set.jpg',
    },
    {
        'name': "Kids' Ethnic Wear Set", 'cat': "Kids' Wear",
        'price': 999, 'disc': 749, 'stock': 30,
        'colors': ['Red/Gold', 'Blue/Gold', 'Green/Gold'],
        'desc': 'Beautiful ethnic set for kids — perfect for festivals like Diwali and Pongal.',
        'img': 'https://images.unsplash.com/photo-1484327973588-c31f829103fe?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1518831959646-742c3a14ebf7?w=600&q=80',
        'file': 'prod_kids_ethnic.jpg',
    },
    {
        'name': "Girls' Casual Dress", 'cat': "Kids' Wear",
        'price': 549, 'disc': 399, 'stock': 55,
        'colors': ['Pink', 'Blue', 'Orange'],
        'desc': 'Comfortable everyday dress for girls. Soft cotton with cute prints.',
        'img': 'https://images.unsplash.com/photo-1518831959646-742c3a14ebf7?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1471286174890-9c112ac6476c?w=600&q=80',
        'file': 'prod_girls_casual.jpg',
    },
    {
        'name': "Boys' Kurta Pyjama Set", 'cat': "Kids' Wear",
        'price': 799, 'disc': 599, 'stock': 35,
        'colors': ['White', 'Cream', 'Sky Blue'],
        'desc': 'Traditional kurta pyjama set for boys. Lightweight and comfortable for festive occasions.',
        'img': 'https://images.unsplash.com/photo-1471286174890-9c112ac6476c?w=600&q=80',
        'fb':  'https://images.unsplash.com/photo-1518831959646-742c3a14ebf7?w=600&q=80',
        'file': 'prod_boys_kurta.jpg',
    },
]

for i, p in enumerate(PRODUCTS, 1):
    img_path = MEDIA / 'products' / p['file']
    dl(p['img'], img_path, p['fb'])

    cat = cat_map.get(p['cat'])
    prod, created = Product.objects.get_or_create(
        name=p['name'],
        defaults={
            'description':    p['desc'],
            'price':          p['price'],
            'discount_price': p['disc'],
            'category':       cat,
            'stock':          p['stock'],
            'status':         'available',
            'colors':         p['colors'],
            'gst_applicable': False,
        }
    )
    if not created:
        prod.price = p['price']
        prod.discount_price = p['disc']
        prod.stock = p['stock']
        prod.colors = p['colors']
        prod.save()

    # attach primary image
    if img_path.exists() and not prod.images.filter(is_primary=True).exists():
        pi = ProductImage(product=prod, is_primary=True, order=0)
        with open(img_path, 'rb') as f:
            pi.image.save(p['file'], File(f), save=False)
        pi.save()

    status = 'created' if created else 'updated'
    print(f'  ✓ [{i:02d}] {p["name"]} — ₹{p["disc"]} (was ₹{p["price"]})')

print(f'\n✓ All done! Kjapperals Garments Store is fully set up.\n')
print('  Run server: $env:ENV_FILE=".env.kjapperals"; python manage.py runserver')
