"""
Setup homepage sections for Kjapperals.
Run: $env:ENV_FILE=".env.kjapperals"; python setup_home_sections.py
"""
import os, sys, django
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'ecommerce.settings')
django.setup()

from shop.models import HomeSection, HomeSectionCategory, HomeSectionProduct, Category, Product

# ── 1. Create all default sections ───────────────────────────────────────────
HomeSection.ensure_defaults()
print('Default sections ready.')

# ── 2. Clear old links ────────────────────────────────────────────────────────
HomeSectionCategory.objects.all().delete()
HomeSectionProduct.objects.all().delete()
print('Old links cleared.')

sec   = {s.title: s for s in HomeSection.objects.all()}
cats  = {c.name: c for c in Category.objects.all()}
prods = {p.name: p for p in Product.objects.filter(status='available')}

def add_cats(title, names):
    s = sec.get(title)
    if not s:
        return
    for i, name in enumerate(names):
        c = cats.get(name)
        if c:
            HomeSectionCategory.objects.get_or_create(section=s, category=c, defaults={'item_order': i})
    print(f'  [cats]  {title}  →  {", ".join(names)}')

def add_prods(title, names):
    s = sec.get(title)
    if not s:
        return
    for i, name in enumerate(names):
        p = prods.get(name)
        if p:
            HomeSectionProduct.objects.get_or_create(section=s, product=p, defaults={'item_order': i})
    print(f'  [prods] {title}  →  {len([n for n in names if n in prods])} products')

# ── Popular Categories ────────────────────────────────────────────────────────
add_cats('Popular Categories', [
    "Women's Ethnic Wear",
    "Women's Western Wear",
    "Men's Formals",
    "Men's Casual Wear",
    "Kids' Wear",
    'Sarees',
])

# ── Trending Categories ───────────────────────────────────────────────────────
add_cats('Trending Categories', [
    'Sarees',
    "Women's Ethnic Wear",
    "Kids' Wear",
    "Men's Casual Wear",
])

# ── New Arrivals (8 latest products) ─────────────────────────────────────────
add_prods('New Arrivals', [
    "Boys' Kurta Pyjama Set",
    "Girls' Casual Dress",
    "Kids' Ethnic Wear Set",
    "Boys' Cotton Shirt & Pant Set",
    "Girls' Party Frock",
    'Casual Check Shirt',
    'Slim Fit Denim Jeans',
    'Casual Round Neck T-Shirt',
])

# ── Trending Products ─────────────────────────────────────────────────────────
add_prods('Trending Products', [
    'Kanjivaram Silk Saree',
    'Embroidered Lehenga Choli',
    'Floral Printed Kurti',
    'Anarkali Churidar Set',
    'Floral Maxi Dress',
    'Party Wear Gown',
    "Men's Cotton Kurta",
    'Classic Formal Shirt',
])

# ── Featured Products ─────────────────────────────────────────────────────────
add_prods('Featured Products', [
    'Kanjivaram Silk Saree',
    'Embroidered Lehenga Choli',
    'Blazer and Trouser Set',
    'Party Wear Gown',
    'Chiffon Designer Saree',
    'Cotton Churidar Suit',
])

# ── Best Sellers ──────────────────────────────────────────────────────────────
add_prods('Best Sellers', [
    'Casual Round Neck T-Shirt',
    'Printed Cotton Saree',
    'Floral Printed Kurti',
    'Slim Fit Denim Jeans',
    'Casual Check Shirt',
    "Girls' Party Frock",
    "Women's Crop Top",
    'Formal Trousers',
])

# ── Summary ───────────────────────────────────────────────────────────────────
print('\nHomepage Sections Summary:')
print(f'  {"Order":<6} {"Title":<25} {"Type":<12} {"Cats":>5} {"Prods":>6} {"Visible":>8}')
print('  ' + '-' * 65)
for s in HomeSection.objects.all().order_by('display_order'):
    print(f'  {s.display_order:<6} {s.title:<25} {s.section_type:<12} '
          f'{s.category_links.count():>5} {s.product_links.count():>6} {str(s.is_visible):>8}')

print('\nDone! Visit http://127.0.0.1:8000 to see the homepage.')
