import calendar
from datetime import datetime, timedelta, time
from decimal import Decimal, InvalidOperation
import re
from textwrap import wrap
from urllib.parse import quote

import bcrypt
from django.contrib import messages
from django.core import signing
from django.db import transaction
from django.db.models import Sum, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST
from django.contrib.auth.models import User
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator

from .forms import CarouselItemForm
from .decorators import admin_login_required
from .tax import calculate_cart_totals, calculate_order_totals, calculate_shipping, money
from .models import (
    AdminUser,
    CarouselItem,
    Category,
    CategoryTag,
    HomeNewsTicker,
    HomeSection,
    GSTConfig,
    GSTRate,
    LoginAttempt,
    NewsletterSubscription,
    Product,
    ProductImage,
    Favorite,
    DeliveryZone,
    DeliveryConfig,
    Order,
    OrderItem,
    OTPSettings,
    StoreAddressSettings,
    ContactMessage,
    Coupon,
    PopupBanner,
    UserProfile,
)
from .email_utils import send_order_status_email


MAX_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


ORDER_WAITING_STATUSES = [
    Order.Status.PENDING,
    Order.Status.PROCESSING,
    Order.Status.SHIPPED,
    Order.Status.RETURN_REQUESTED,
]


def _admin_order_status_choices():
    return [
        (value, 'Accepted' if value == Order.Status.PROCESSING else label)
        for value, label in Order.Status.choices
    ]


def _admin_order_status_label(status):
    return dict(_admin_order_status_choices()).get(status, status)


def _admin_visible_orders():
    return Order.objects.exclude(
        payment_method='PhonePe',
        payment_status__in=[
            Order.PaymentStatus.PENDING,
            Order.PaymentStatus.FAILED,
            Order.PaymentStatus.CANCELLED,
            Order.PaymentStatus.TIMEOUT,
        ],
    )


def _admin_countable_orders():
    return _admin_visible_orders().exclude(
        status__in=[Order.Status.DELIVERED, Order.Status.RETURNED],
    )


def _admin_waiting_orders():
    return _admin_visible_orders().filter(status__in=ORDER_WAITING_STATUSES)


def _find_matching_delivery_zone(country, state, county='', city=''):
    allowed_zones = DeliveryZone.objects.filter(is_active=True)
    if not allowed_zones.exists():
        return None, True

    matching_zones = allowed_zones.filter(country__iexact=country, state__iexact=state)
    for zone in matching_zones:
        if zone.county and zone.county.lower() != (county or '').lower():
            continue
        if zone.city and zone.city.lower() != (city or '').lower():
            continue
        return zone, True
    return None, False


def _admin_billing_user(full_name, phone, email=''):
    clean_phone = re.sub(r'\D+', '', phone or '') or 'walkin'
    clean_email = (email or '').strip().lower()
    if clean_email:
        existing = User.objects.filter(email__iexact=clean_email).first()
        if existing:
            return existing

    username = f'admin_bill_{clean_phone}'[:140]
    user, _ = User.objects.get_or_create(
        username=username,
        defaults={
            'email': clean_email or f'{username}@admin-billing.local',
            'first_name': (full_name or 'Admin Billing')[:150],
        },
    )
    update_fields = []
    if clean_email and user.email != clean_email:
        user.email = clean_email
        update_fields.append('email')
    if full_name and user.first_name != full_name[:150]:
        user.first_name = full_name[:150]
        update_fields.append('first_name')
    if update_fields:
        user.save(update_fields=update_fields)
    return user


def _get_ip(request):
    x = request.META.get('HTTP_X_FORWARDED_FOR')
    return x.split(',')[0].strip() if x else request.META.get('REMOTE_ADDR', '0.0.0.0')


def _is_locked(ip):
    cutoff = timezone.now() - timedelta(minutes=LOCKOUT_MINUTES)
    return LoginAttempt.objects.filter(
        ip_address=ip, success=False, attempted_at__gte=cutoff
    ).count() >= MAX_ATTEMPTS


# ─── Login / Logout ──────────────────────────────────────────
@csrf_protect
@require_http_methods(['GET', 'POST'])
def admin_login(request):
    if request.session.get('admin_authenticated'):
        return redirect('admin_dashboard')
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        ip = _get_ip(request)
        if _is_locked(ip):
            messages.error(request, 'Too many attempts. Wait 15 minutes.')
            return render(request, 'adminpanel/admin_login.html', {'locked': True})
        try:
            user = AdminUser.objects.get(username=username, is_active=True)
            if bcrypt.checkpw(password.encode(), user.password.encode()):
                user.last_login = timezone.now()
                user.save(update_fields=['last_login'])
                LoginAttempt.objects.create(username=username, ip_address=ip, success=True)
                request.session['admin_authenticated'] = True
                request.session['admin_id'] = user.id
                request.session['admin_name'] = user.username
                request.session.set_expiry(3600)
                return redirect('admin_dashboard')
        except AdminUser.DoesNotExist:
            pass
        LoginAttempt.objects.create(username=username, ip_address=ip, success=False)
        messages.error(request, 'Invalid username or password.')
    return render(request, 'adminpanel/admin_login.html')


def admin_logout(request):
    request.session.flush()
    return redirect('admin_login')


# ─── Dashboard ───────────────────────────────────────────────
@admin_login_required
def admin_dashboard(request):
    now = timezone.now()
    week = now - timedelta(days=7)
    admin = get_object_or_404(AdminUser, pk=request.session['admin_id'])

    products = Product.objects.select_related('category').prefetch_related('images').order_by('-created_at')[:10]
    product_count = Product.objects.count()

    # Dynamic Order Stats
    total_orders = _admin_countable_orders().count()
    sales_agg = Order.objects.exclude(status=Order.Status.CANCELLED).aggregate(total_sum=Sum('total'))
    total_sales = float(sales_agg['total_sum'] or 0.0)
    
    visible_orders = _admin_countable_orders()
    pending_orders = visible_orders.filter(status=Order.Status.PENDING).count()
    low_stock_products = Product.objects.filter(stock__lte=5).count()
    
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    monthly_rev_agg = Order.objects.filter(created_at__gte=month_start).exclude(status=Order.Status.CANCELLED).aggregate(total_sum=Sum('total'))
    revenue_this_month = float(monthly_rev_agg['total_sum'] or 0.0)
    
    db_recent_orders = Order.objects.order_by('-created_at')[:5]
    recent_orders = []
    for o in db_recent_orders:
        o.customer_name = o.full_name
        recent_orders.append(o)
        
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    order_statistics = {
        'today': visible_orders.filter(created_at__gte=today_start).count(),
        'this_week': visible_orders.filter(created_at__gte=week).count(),
        'this_month': visible_orders.filter(created_at__gte=month_start).count(),
        'pending': pending_orders,
    }
    
    top_selling_data = OrderItem.objects.values('product__name').annotate(sold=Sum('quantity')).order_by('-sold')[:5]
    top_selling = [{'name': item['product__name'], 'sold': item['sold']} for item in top_selling_data if item['product__name']]
    
    sales_periods = ('day', 'week', 'month', 'year')
    requested_sales_period = request.GET.get('sales_period', 'day')
    if requested_sales_period not in sales_periods:
        requested_sales_period = 'day'

    sales_qs = Order.objects.exclude(status=Order.Status.CANCELLED)

    def month_bounds(year, month):
        start = now.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)
        if month == 12:
            end = start.replace(year=year + 1, month=1)
        else:
            end = start.replace(month=month + 1)
        return start, end

    def build_sales_chart(period):
        rows = []
        if period == 'day':
            for i in range(6, -1, -1):
                day = now - timedelta(days=i)
                start = day.replace(hour=0, minute=0, second=0, microsecond=0)
                rows.append((day.strftime('%a'), start, start + timedelta(days=1)))
        elif period == 'week':
            current_week = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
            for i in range(7, -1, -1):
                start = current_week - timedelta(weeks=i)
                rows.append((start.strftime('%d %b'), start, start + timedelta(days=7)))
        elif period == 'month':
            month_index = now.year * 12 + now.month - 1
            for i in range(11, -1, -1):
                index = month_index - i
                year = index // 12
                month = index % 12 + 1
                start, end = month_bounds(year, month)
                rows.append((start.strftime('%b'), start, end))
        else:
            for year in range(now.year - 4, now.year + 1):
                start = now.replace(year=year, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
                end = start.replace(year=year + 1)
                rows.append((str(year), start, end))

        chart = []
        for label, start, end in rows:
            amount = sales_qs.filter(created_at__gte=start, created_at__lt=end).aggregate(total_sum=Sum('total'))['total_sum'] or 0.0
            chart.append({'label': label, 'amount': float(amount), 'value': 0})

        total = sum(item['amount'] for item in chart)
        max_val = max((item['amount'] for item in chart), default=0)
        if max_val > 0:
            for item in chart:
                item['value'] = round((item['amount'] / max_val) * 90) + 10 if item['amount'] else 4
        return chart, total

    requested_index = sales_periods.index(requested_sales_period)
    sales_chart = []
    sales_period = requested_sales_period
    sales_period_auto = False
    for period in sales_periods[requested_index:]:
        chart, total = build_sales_chart(period)
        if total > 0:
            sales_chart = chart
            sales_period = period
            sales_period_auto = period != requested_sales_period
            break

    sales_period_labels = {
        'day': 'Day wise',
        'week': 'Week wise',
        'month': 'Month wise',
        'year': 'Year wise',
    }

    return render(request, 'adminpanel/dashboard.html', {
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'dashboard',
        'last_login': admin.last_login,
        'products': products,
        'product_count': product_count,
        'total_sales': total_sales,
        'total_orders': total_orders,
        'pending_orders': pending_orders,
        'low_stock_products': low_stock_products,
        'revenue_this_month': revenue_this_month,
        'sales_chart': sales_chart,
        'sales_period': sales_period,
        'sales_period_label': sales_period_labels[sales_period],
        'sales_period_auto': sales_period_auto,
        'order_statistics': order_statistics,
        'top_selling': top_selling,
        'recent_orders': recent_orders,
    })


# ─── Product List ─────────────────────────────────────────────
@admin_login_required
def admin_products(request):
    products = Product.objects.select_related('category').prefetch_related('images').order_by('-created_at')
    categories = Category.objects.order_by('name')

    cat = request.GET.get('category')
    status = request.GET.get('status')
    search = request.GET.get('search')
    if cat:
        products = products.filter(category__id=cat)
    if status:
        products = products.filter(status=status)
    if search:
        products = products.filter(name__icontains=search)

    paginator = Paginator(products, 15)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'adminpanel/products.html', {
        'products': page_obj.object_list,
        'page_obj': page_obj,
        'page_range': paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1),
        'query_without_page': _revenue_querystring(request, page=''),
        'categories': categories,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'products',
        'product_count': Product.objects.count(),
        'filter_category': cat or '',
        'filter_status': status or '',
        'filter_search': search or '',
    })


# ─── Add Product ─────────────────────────────────────────────
@admin_login_required
def add_product(request):
    categories = Category.objects.filter(status=Category.Status.ACTIVE).order_by('name')
    gst_rates = GSTRate.objects.filter(is_active=True).order_by('percentage', 'name')
    return render(request, 'adminpanel/add_product.html', {
        'categories': categories,
        'gst_rates': gst_rates,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'products',
        'product_count': Product.objects.count(),
    })


# ─── Edit Product ─────────────────────────────────────────────
@admin_login_required
def edit_product(request, pk):
    product = get_object_or_404(Product.objects.select_related('category'), pk=pk)
    categories = Category.objects.filter(status=Category.Status.ACTIVE).order_by('name')
    gst_rates = GSTRate.objects.filter(is_active=True).order_by('percentage', 'name')
    if product.category_id and product.category.status != Category.Status.ACTIVE:
        categories = Category.objects.filter(pk=product.category_id) | categories

    if request.method == 'POST':
        product.name = request.POST.get('name', product.name).strip()
        product.description = request.POST.get('description', product.description).strip()
        product.price = request.POST.get('price', product.price)
        product.discount_price = request.POST.get('discount_price') or None
        product.category_id = request.POST.get('category') or None
        product.stock = request.POST.get('stock', product.stock)
        product.status = request.POST.get('status', product.status)

        colors_raw = request.POST.get('colors', '')
        product.colors = [c.strip() for c in colors_raw.split(',') if c.strip()]

        product.save()

        new_images = request.FILES.getlist('images')
        existing = product.images.count()
        for i, img in enumerate(new_images):
            ProductImage.objects.create(
                product=product, image=img,
                is_primary=False, order=existing + i
            )

        delete_ids = request.POST.getlist('delete_images')
        for img_id in delete_ids:
            try:
                img = ProductImage.objects.get(pk=img_id, product=product)
                img.image.delete(save=False)
                img.delete()
            except ProductImage.DoesNotExist:
                pass

        primary_id = request.POST.get('primary_image')
        if primary_id:
            try:
                pi = ProductImage.objects.get(pk=primary_id, product=product)
                pi.is_primary = True
                pi.save()
            except ProductImage.DoesNotExist:
                pass

        return redirect('/admin-panel/products/?msg=Product+updated&type=success')

    return render(request, 'adminpanel/edit_product.html', {
        'product': product,
        'categories': categories,
        'gst_rates': gst_rates,
        'colors_str': ', '.join(product.colors) if product.colors else '',
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'products',
        'product_count': Product.objects.count(),
    })


# ─── Delete Product ───────────────────────────────────────────
@admin_login_required
def admin_categories(request):
    CategoryTag.ensure_defaults()
    categories = Category.objects.prefetch_related('products', 'tags').order_by('name')
    tags = CategoryTag.objects.prefetch_related('categories').order_by('name')
    gst_rates = GSTRate.objects.filter(is_active=True).order_by('percentage', 'name')
    status = request.GET.get('status')
    search = request.GET.get('search')
    if status:
        categories = categories.filter(status=status)
    if search:
        categories = categories.filter(name__icontains=search)

    return render(request, 'adminpanel/categories.html', {
        'categories': categories,
        'category_tags': tags,
        'gst_rates': gst_rates,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'categories',
        'category_count': Category.objects.count(),
        'product_count': Product.objects.count(),
        'filter_status': status or '',
        'filter_search': search or '',
    })


# Delete Product
@admin_login_required
@require_POST
def delete_product(request, pk):
    product = get_object_or_404(Product, pk=pk)
    for img in product.images.all():
        img.image.delete(save=False)
    product.delete()
    return redirect('/admin-panel/products/?msg=Product+deleted&type=success')


# ─── Login Logs ───────────────────────────────────────────────
@admin_login_required
def admin_carousel(request):
    items = CarouselItem.objects.select_related('product')
    return render(request, 'adminpanel/carousel_items.html', {
        'items': items,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'carousel',
        'carousel_count': items.count(),
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def add_carousel_item(request):
    form = CarouselItemForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, 'Carousel item created.')
        return redirect('admin_carousel')
    return render(request, 'adminpanel/carousel_form.html', {
        'form': form,
        'form_title': 'Add carousel item',
        'submit_text': 'Add item',
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'carousel',
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def edit_carousel_item(request, pk):
    item = get_object_or_404(CarouselItem, pk=pk)
    old_image = item.image
    form = CarouselItemForm(request.POST or None, request.FILES or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        updated = form.save()
        if request.FILES.get('image') and old_image and old_image.name != updated.image.name:
            old_image.delete(save=False)
        messages.success(request, 'Carousel item updated.')
        return redirect('admin_carousel')
    return render(request, 'adminpanel/carousel_form.html', {
        'form': form,
        'item': item,
        'form_title': 'Edit carousel item',
        'submit_text': 'Save changes',
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'carousel',
    })


@admin_login_required
@require_POST
def delete_carousel_item(request, pk):
    item = get_object_or_404(CarouselItem, pk=pk)
    if item.image:
        item.image.delete(save=False)
    item.delete()
    messages.success(request, 'Carousel item deleted.')
    return redirect('admin_carousel')


@admin_login_required
@require_http_methods(['GET', 'POST'])
def admin_settings(request):
    otp_settings = OTPSettings.current()
    gst_config = GSTConfig.current()
    store_address_settings = StoreAddressSettings.current()
    gst_rates = GSTRate.objects.all()

    def save_store_address_settings(data, files=None):
        sender_name = data.get('sender_name', '').strip() or 'Kj Apperals'
        sender_phone = data.get('sender_phone', '').strip()
        sender_address = data.get('sender_address', '').strip()
        contact_email = data.get('contact_email', '').strip()
        contact_phone = data.get('contact_phone', '').strip()
        whatsapp_number = data.get('whatsapp_number', '').strip()
        business_hours = data.get('business_hours', '').strip()
        instagram_url = data.get('instagram_url', '').strip()
        facebook_url = data.get('facebook_url', '').strip()
        pinterest_url = data.get('pinterest_url', '').strip()
        youtube_url = data.get('youtube_url', '').strip()
        x_url = data.get('x_url', '').strip()
        cod_enabled = str(data.get('cod_enabled', 'true')).lower() in ('1', 'true', 'on', 'yes')
        try:
            cod_extra_charge = Decimal(str(data.get('cod_extra_charge', 0) or 0))
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({'success': False, 'message': 'Enter a valid COD extra charge.'}, status=400)
        if cod_extra_charge < 0:
            return JsonResponse({'success': False, 'message': 'COD extra charge cannot be negative.'}, status=400)

        if str(data.get('clear_brand_logo', '')).lower() in ('1', 'true', 'on', 'yes') and store_address_settings.brand_logo:
            store_address_settings.brand_logo.delete(save=False)

        uploaded_logo = files.get('brand_logo') if files else None
        if uploaded_logo:
            if store_address_settings.brand_logo:
                store_address_settings.brand_logo.delete(save=False)
            store_address_settings.brand_logo = uploaded_logo

        store_address_settings.sender_name = sender_name
        store_address_settings.sender_phone = sender_phone
        store_address_settings.sender_address = sender_address
        store_address_settings.contact_email = contact_email
        store_address_settings.contact_phone = contact_phone
        store_address_settings.whatsapp_number = whatsapp_number
        store_address_settings.business_hours = business_hours
        store_address_settings.instagram_url = instagram_url
        store_address_settings.facebook_url = facebook_url
        store_address_settings.pinterest_url = pinterest_url
        store_address_settings.youtube_url = youtube_url
        store_address_settings.x_url = x_url
        store_address_settings.cod_enabled = cod_enabled
        store_address_settings.cod_extra_charge = cod_extra_charge
        store_address_settings.save()
        return JsonResponse({
            'success': True,
            'message': 'Store contact, logo, social, and COD settings updated.',
            'store_address_settings': {
                'sender_name': store_address_settings.sender_name,
                'brand_logo_url': store_address_settings.brand_logo.url if store_address_settings.brand_logo else '',
                'sender_phone': store_address_settings.sender_phone,
                'sender_address': store_address_settings.sender_address,
                'contact_email': store_address_settings.contact_email,
                'contact_phone': store_address_settings.contact_phone,
                'whatsapp_number': store_address_settings.whatsapp_number,
                'business_hours': store_address_settings.business_hours,
                'instagram_url': store_address_settings.instagram_url,
                'facebook_url': store_address_settings.facebook_url,
                'pinterest_url': store_address_settings.pinterest_url,
                'youtube_url': store_address_settings.youtube_url,
                'x_url': store_address_settings.x_url,
                'cod_enabled': store_address_settings.cod_enabled,
                'cod_extra_charge': float(store_address_settings.cod_extra_charge),
            }
        })

    if request.method == 'POST':
        # Check if JSON request
        if 'application/json' in (request.content_type or ''):
            import json
            try:
                data = json.loads(request.body)
            except json.JSONDecodeError:
                return JsonResponse({'success': False, 'message': 'Invalid JSON data.'}, status=400)

            if data.get('form_type') == 'store_address_settings':
                return save_store_address_settings(data)

            if data.get('form_type') == 'gst_settings':
                default_rate = None
                default_rate_id = data.get('default_rate')
                if default_rate_id:
                    default_rate = GSTRate.objects.filter(pk=default_rate_id).first()
                    if not default_rate:
                        return JsonResponse({'success': False, 'message': 'Select a valid default GST rate.'}, status=400)
                pricing_mode = data.get('pricing_mode', GSTConfig.PricingMode.EXCLUSIVE)
                if pricing_mode not in dict(GSTConfig.PricingMode.choices):
                    return JsonResponse({'success': False, 'message': 'Invalid GST pricing mode.'}, status=400)
                gst_config.enabled = bool(data.get('enabled'))
                gst_config.seller_state = data.get('seller_state', '').strip() or 'Tamil Nadu'
                gst_config.default_rate = default_rate
                gst_config.pricing_mode = pricing_mode
                gst_config.seller_name = data.get('seller_name', '').strip() or 'Kj Apperals'
                gst_config.seller_gstin = data.get('seller_gstin', '').strip()
                gst_config.seller_address = data.get('seller_address', '').strip()
                gst_config.save()
                return JsonResponse({'success': True, 'message': 'GST configuration updated.'})
            
            company_name = data.get('company_name', '').strip() or 'Kj Apperals'
            sender_email = data.get('sender_email', '').strip()
            smtp_host_user = data.get('smtp_host_user', '').strip()
            smtp_host_password = data.get('smtp_host_password', '').strip()
            try:
                otp_length = int(data.get('otp_length', 6))
                otp_expiry_minutes = int(data.get('otp_expiry_minutes', 5))
                resend_cooldown_seconds = int(data.get('resend_cooldown_seconds', 60))
            except (ValueError, TypeError):
                return JsonResponse({'success': False, 'message': 'Invalid numbers for OTP configuration.'}, status=400)

            if not sender_email or '@' not in sender_email:
                return JsonResponse({'success': False, 'message': 'Please enter a valid sender email address.'}, status=400)

            if otp_length not in [4, 6, 8]:
                return JsonResponse({'success': False, 'message': 'OTP length must be 4, 6, or 8 digits.'}, status=400)

            if otp_expiry_minutes < 1 or otp_expiry_minutes > 60:
                return JsonResponse({'success': False, 'message': 'OTP expiry must be between 1 and 60 minutes.'}, status=400)

            if resend_cooldown_seconds < 10 or resend_cooldown_seconds > 600:
                return JsonResponse({'success': False, 'message': 'Resend cooldown must be between 10 and 600 seconds.'}, status=400)

            otp_settings.company_name = company_name
            otp_settings.sender_email = sender_email
            if smtp_host_user:
                otp_settings.smtp_host_user = smtp_host_user
            if smtp_host_password:
                otp_settings.smtp_host_password = smtp_host_password
            otp_settings.otp_length = otp_length
            otp_settings.otp_expiry_minutes = otp_expiry_minutes
            otp_settings.resend_cooldown_seconds = resend_cooldown_seconds
            otp_settings.save()

            return JsonResponse({
                'success': True,
                'message': 'OTP & SMTP settings updated successfully!',
                'otp_settings': {
                    'company_name': otp_settings.company_name,
                    'sender_email': otp_settings.sender_email,
                    'smtp_host_user': otp_settings.smtp_host_user,
                    'smtp_host_password': otp_settings.smtp_host_password,
                    'otp_length': otp_settings.otp_length,
                    'otp_expiry_minutes': otp_settings.otp_expiry_minutes,
                    'resend_cooldown_seconds': otp_settings.resend_cooldown_seconds,
                }
            })
        else:
            if request.POST.get('form_type') == 'store_address_settings':
                return save_store_address_settings(request.POST, request.FILES)

            company_name = request.POST.get('company_name', '').strip() or 'Kj Apperals'
            sender_email = request.POST.get('sender_email', '').strip()
            smtp_host_user = request.POST.get('smtp_host_user', '').strip()
            smtp_host_password = request.POST.get('smtp_host_password', '').strip()
            try:
                otp_length = int(request.POST.get('otp_length', 6))
                otp_expiry_minutes = int(request.POST.get('otp_expiry_minutes', 5))
                resend_cooldown_seconds = int(request.POST.get('resend_cooldown_seconds', 60))
            except (ValueError, TypeError):
                messages.error(request, 'Please enter valid numeric values for OTP settings.')
                return redirect('admin_settings')

            if not sender_email or '@' not in sender_email:
                messages.error(request, 'Please enter a valid sender email address.')
                return redirect('admin_settings')

            if otp_length not in [4, 6, 8]:
                messages.error(request, 'OTP length must be 4, 6, or 8 digits.')
                return redirect('admin_settings')

            otp_settings.company_name = company_name
            otp_settings.sender_email = sender_email
            if smtp_host_user:
                otp_settings.smtp_host_user = smtp_host_user
            if smtp_host_password:
                otp_settings.smtp_host_password = smtp_host_password
            otp_settings.otp_length = otp_length
            otp_settings.otp_expiry_minutes = otp_expiry_minutes
            otp_settings.resend_cooldown_seconds = resend_cooldown_seconds
            otp_settings.save()

            messages.success(request, 'OTP & Security Settings updated successfully.')
            return redirect('admin_settings')

    return render(request, 'adminpanel/settings.html', {
        'otp_settings': otp_settings,
        'gst_config': gst_config,
        'store_address_settings': store_address_settings,
        'gst_rates': gst_rates,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'settings',
    })


@admin_login_required
def admin_home_sections(request):
    HomeSection.ensure_defaults()
    return render(request, 'adminpanel/home_sections.html', {
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'home_sections',
        'section_count': HomeSection.objects.count(),
    })


@admin_login_required
def admin_news_tickers(request):
    search = request.GET.get('search', '').strip()
    tickers = HomeNewsTicker.objects.all().order_by('display_order', 'id')
    if search:
        tickers = tickers.filter(message__icontains=search)
    tickers = list(tickers)
    product_slugs = [
        ticker.link_url.strip('/').split('/')[-1]
        for ticker in tickers
        if ticker.link_url.startswith('/products/') and ticker.link_url.endswith('/')
    ]
    products_by_slug = {
        product.slug: product.name
        for product in Product.objects.filter(slug__in=product_slugs)
    }
    for ticker in tickers:
        ticker.target_product_name = ''
        if ticker.link_url.startswith('/products/') and ticker.link_url.endswith('/'):
            ticker.target_product_name = products_by_slug.get(ticker.link_url.strip('/').split('/')[-1], '')
    return render(request, 'adminpanel/news_tickers.html', {
        'tickers': tickers,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'news_tickers',
        'ticker_count': HomeNewsTicker.objects.count(),
        'active_ticker_count': HomeNewsTicker.objects.filter(is_active=True).count(),
        'filter_search': search,
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def add_news_ticker(request):
    if request.method == 'POST':
        message_text = request.POST.get('message', '').strip()
        link_url = request.POST.get('link_url', '').strip()
        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == '1'

        if not message_text:
            messages.error(request, 'Ticker message is required.')
            return redirect('add_news_ticker')

        next_order = (HomeNewsTicker.objects.order_by('-display_order').values_list('display_order', flat=True).first() or 0) + 10
        HomeNewsTicker.objects.create(
            message=message_text,
            link_url=link_url,
            display_order=next_order,
            is_active=is_active,
        )
        messages.success(request, 'News ticker created.')
        return redirect('admin_news_tickers')

    return render(request, 'adminpanel/news_ticker_form.html', {
        'form_title': 'Add News Ticker',
        'submit_text': 'Create Ticker',
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'news_tickers',
        'products': Product.objects.filter(status=Product.Status.AVAILABLE).order_by('name'),
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def edit_news_ticker(request, pk):
    ticker = get_object_or_404(HomeNewsTicker, pk=pk)
    if request.method == 'POST':
        message_text = request.POST.get('message', '').strip()
        link_url = request.POST.get('link_url', '').strip()
        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == '1'

        if not message_text:
            messages.error(request, 'Ticker message is required.')
            return redirect('edit_news_ticker', pk=pk)

        ticker.message = message_text
        ticker.link_url = link_url
        ticker.is_active = is_active
        ticker.save()
        messages.success(request, 'News ticker updated.')
        return redirect('admin_news_tickers')

    selected_product = None
    product_slug = ''
    if ticker.link_url.startswith('/products/') and ticker.link_url.endswith('/'):
        product_slug = ticker.link_url.strip('/').split('/')[-1]
        selected_product = Product.objects.filter(slug=product_slug).first()

    return render(request, 'adminpanel/news_ticker_form.html', {
        'ticker': ticker,
        'form_title': 'Edit News Ticker',
        'submit_text': 'Save Changes',
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'news_tickers',
        'products': Product.objects.filter(status=Product.Status.AVAILABLE).order_by('name'),
        'selected_product': selected_product,
    })


@admin_login_required
@require_POST
def delete_news_ticker(request, pk):
    ticker = get_object_or_404(HomeNewsTicker, pk=pk)
    ticker.delete()
    messages.success(request, 'News ticker deleted.')
    return redirect('admin_news_tickers')


@admin_login_required
@require_POST
def toggle_news_ticker(request, pk):
    ticker = get_object_or_404(HomeNewsTicker, pk=pk)
    ticker.is_active = not ticker.is_active
    ticker.save(update_fields=['is_active', 'updated_at'])
    messages.success(request, f"News ticker {'enabled' if ticker.is_active else 'disabled'}.")
    return redirect('admin_news_tickers')


@admin_login_required
@require_POST
def move_news_ticker(request, pk, direction):
    ticker = get_object_or_404(HomeNewsTicker, pk=pk)
    ordered = list(HomeNewsTicker.objects.order_by('display_order', 'id'))
    index = next((idx for idx, item in enumerate(ordered) if item.pk == ticker.pk), None)
    if index is None:
        return redirect('admin_news_tickers')

    swap_index = index - 1 if direction == 'up' else index + 1
    if 0 <= swap_index < len(ordered):
        other = ordered[swap_index]
        ticker.display_order, other.display_order = other.display_order, ticker.display_order
        ticker.save(update_fields=['display_order', 'updated_at'])
        other.save(update_fields=['display_order', 'updated_at'])
        messages.success(request, 'Ticker order updated.')
    return redirect('admin_news_tickers')


@admin_login_required
def admin_subscriptions(request):
    search = request.GET.get('search', '').strip()
    subscriptions = NewsletterSubscription.objects.all()
    if search:
        subscriptions = subscriptions.filter(email__icontains=search)
    return render(request, 'adminpanel/subscriptions.html', {
        'subscriptions': subscriptions,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'subscriptions',
        'subscription_count': NewsletterSubscription.objects.count(),
        'filter_search': search,
    })


@admin_login_required
@require_POST
def delete_subscription(request, pk):
    subscription = get_object_or_404(NewsletterSubscription, pk=pk)
    email = subscription.email
    subscription.delete()
    messages.success(request, f'Subscription for {email} deleted.')
    return redirect('admin_subscriptions')


@admin_login_required
def admin_customers(request):
    from django.db.models import Count

    search = request.GET.get('search', '').strip()
    customers = User.objects.filter(is_superuser=False, is_staff=False).select_related('profile').annotate(
        favorites_count=Count('favorites')
    ).order_by('-date_joined')

    if search:
        customers = customers.filter(email__icontains=search)

    customer_list = list(customers)
    customer_ids = [customer.id for customer in customer_list]

    orders = Order.objects.filter(user_id__in=customer_ids).prefetch_related('items').order_by('-created_at')
    orders_by_customer = {}
    for order in orders:
        orders_by_customer.setdefault(order.user_id, []).append(order)

    shop_url = request.build_absolute_uri(reverse('shop_all'))

    def absolute_url(path):
        return request.build_absolute_uri(path)

    def product_payload(product):
        product_url = absolute_url(reverse('product_detail', args=[product.slug])) if product.slug else shop_url
        price = product.discount_price if product.discount_price else product.price
        return {
            'id': product.id,
            'name': product.name,
            'price': str(price),
            'url': product_url,
        }

    products = list(Product.objects.filter(status=Product.Status.AVAILABLE).order_by('-created_at')[:30])
    latest_products = [product_payload(product) for product in products[:12]]

    trending_products_qs = Product.objects.filter(
        status=Product.Status.AVAILABLE,
        home_sections__title__iexact='Trending Products',
        home_sections__is_visible=True,
    ).distinct().order_by('-created_at')
    trending_products = [product_payload(product) for product in trending_products_qs[:12]]

    coupons = Coupon.objects.filter(is_active=True, valid_until__gte=timezone.now()).order_by('-created_at')[:20]
    offers = []
    for coupon in coupons:
        discount_value = f"{coupon.discount_value:.2f}".rstrip('0').rstrip('.')
        discount = (
            f"{discount_value}%"
            if coupon.discount_type == Coupon.DiscountType.PERCENTAGE
            else f"Rs. {discount_value}"
        )
        offers.append({
            'id': f'coupon-{coupon.id}',
            'title': coupon.code,
            'description': f"Use coupon {coupon.code}"
                           + (f" on orders above Rs. {coupon.min_purchase}." if coupon.min_purchase else "."),
            'discount': discount,
            'valid_until': timezone.localtime(coupon.valid_until).strftime('%d %b %Y, %I:%M %p'),
            'link': shop_url,
        })

    popup_offers = PopupBanner.objects.filter(is_active=True).order_by('-created_at')[:10]
    for popup in popup_offers:
        link = popup.link_url or reverse('shop_all')
        offers.append({
            'id': f'popup-{popup.id}',
            'title': popup.title or f'Offer #{popup.id}',
            'description': popup.subtitle or 'A special store offer is available for you.',
            'discount': '',
            'valid_until': 'Limited time',
            'link': absolute_url(link) if link.startswith('/') else link,
        })

    whatsapp_customers = []
    for customer in customer_list:
        customer_orders = orders_by_customer.get(customer.id, [])
        latest_order = customer_orders[0] if customer_orders else None
        try:
            profile = customer.profile
        except UserProfile.DoesNotExist:
            profile = None
        full_name = (customer.get_full_name() or '').strip()
        if not full_name and latest_order:
            full_name = latest_order.full_name
        if not full_name:
            full_name = customer.email.split('@')[0] if customer.email else f'Customer #{customer.id}'
        phone = (getattr(profile, 'phone', '') or '').strip()
        country = (getattr(profile, 'country', '') or '').strip() or 'India'
        if not phone and latest_order:
            phone = latest_order.phone
            country = latest_order.country
        whatsapp_customers.append({
            'id': customer.id,
            'name': full_name,
            'email': customer.email,
            'phone': phone,
            'whatsappPhone': _whatsapp_phone_number(phone, country),
            'orders': [
                {
                    'id': order.id,
                    'number': str(order.id),
                    'amount': str(order.total),
                    'status': order.get_status_display(),
                    'created_at': timezone.localtime(order.created_at).strftime('%d %b %Y, %I:%M %p'),
                    'items': ', '.join(item.product_name for item in order.items.all()[:3]),
                }
                for order in customer_orders[:10]
            ],
        })

    return render(request, 'adminpanel/customers.html', {
        'customers': customer_list,
        'whatsapp_customers': whatsapp_customers,
        'whatsapp_offers': offers,
        'whatsapp_latest_products': latest_products,
        'whatsapp_trending_products': trending_products,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'customers',
        'customer_count': len(customer_list),
        'filter_search': search,
    })


@admin_login_required
@require_POST
def delete_customer(request, pk):
    from django.contrib.auth.models import User
    customer = get_object_or_404(User, pk=pk, is_superuser=False, is_staff=False)
    email = customer.email
    customer.delete()
    messages.success(request, f'Customer account for {email} has been deleted.')
    return redirect('admin_customers')


# ─── Delivery Settings & Zones Views ──────────────────────────

@admin_login_required
@require_http_methods(['GET', 'POST'])
def admin_delivery(request):
    config = DeliveryConfig.current()
    zones = DeliveryZone.objects.all()
    
    if request.method == 'POST':
        config.fee_type = request.POST.get('fee_type', config.fee_type)
        try:
            config.base_fee = float(request.POST.get('base_fee', config.base_fee))
            config.free_threshold = float(request.POST.get('free_threshold', config.free_threshold))
        except ValueError:
            messages.error(request, 'Please enter valid numbers for fee values.')
            return redirect('admin_delivery')
            
        config.save()
        messages.success(request, 'Delivery configuration updated successfully.')
        return redirect('admin_delivery')
        
    return render(request, 'adminpanel/delivery.html', {
        'config': config,
        'zones': zones,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'delivery',
    })


@admin_login_required
@require_POST
def add_delivery_zone(request):
    country = request.POST.get('country', 'India').strip()
    state = request.POST.get('state', '').strip()
    city = request.POST.get('city', '').strip()
    county = request.POST.get('county', '').strip()
    
    try:
        delivery_fee = float(request.POST.get('delivery_fee', 150.00))
    except ValueError:
        delivery_fee = 150.00
        
    if not state:
        messages.error(request, 'State field is required to add a delivery zone.')
        return redirect('admin_delivery')
        
    DeliveryZone.objects.create(
        country=country,
        state=state,
        city=city,
        county=county,
        delivery_fee=delivery_fee,
        is_active=True
    )
    messages.success(request, f'Delivery zone for {state} added successfully.')
    return redirect('admin_delivery')


@admin_login_required
@require_POST
def delete_delivery_zone(request, pk):
    zone = get_object_or_404(DeliveryZone, pk=pk)
    details = str(zone)
    zone.delete()
    messages.success(request, f'Delivery zone [{details}] deleted successfully.')
    return redirect('admin_delivery')


# ─── Order Management Views ───────────────────────────────────

def _whatsapp_phone_number(raw_phone, country='India'):
    digits = re.sub(r'\D+', '', raw_phone or '')
    if not digits:
        return ''
    if digits.startswith('00'):
        return digits[2:]
    if len(digits) == 10:
        country_code_map = {
            'india': '91',
            'united states': '1',
            'usa': '1',
            'us': '1',
        }
        return f"{country_code_map.get((country or '').strip().lower(), '91')}{digits}"
    return digits


def _invoice_available_for_whatsapp(order):
    if order.payment_status == Order.PaymentStatus.PAID:
        return True
    return order.payment_method.upper() == 'COD' and order.status == Order.Status.DELIVERED


def _build_shared_invoice_url(request, order):
    if not _invoice_available_for_whatsapp(order):
        return ''
    token = signing.TimestampSigner(salt='shop.invoice.share').sign(str(order.id))
    path = reverse('download_shared_invoice', kwargs={'order_id': order.id, 'token': token})
    return request.build_absolute_uri(path)


def _build_order_whatsapp_url(order, store_address_settings, request=None, include_invoice=False):
    if order.status == Order.Status.PROCESSING and not include_invoice:
        return ''

    customer_phone = _whatsapp_phone_number(order.phone, order.country)
    if not customer_phone:
        return ''

    store_name = (store_address_settings.sender_name or '').strip() or 'Store'
    store_phone = (store_address_settings.sender_phone or '').strip()
    invoice_url = _build_shared_invoice_url(request, order) if request and include_invoice else ''
    invoice_number = f"INV-{order.created_at:%Y%m%d}-{order.id:06d}"
    status_messages = {
        Order.Status.PENDING: f"Your order from {store_name} is pending payment.",
        Order.Status.PROCESSING: f"Your order from {store_name} is now being processed.",
        Order.Status.SHIPPED: f"Your order from {store_name} has been shipped.",
        Order.Status.DELIVERED: f"Your order from {store_name} has been delivered.",
        Order.Status.CANCELLED: f"Your order from {store_name} has been cancelled.",
        Order.Status.RETURN_REQUESTED: f"Your replacement request for {store_name} is under review.",
        Order.Status.RETURNED: f"Your replacement for {store_name} has been completed successfully.",
    }
    item_lines = [
        f"- {item.product_name} x {item.quantity} = Rs. {item.final_amount or (item.price * item.quantity)}"
        for item in order.items.all()
    ]
    if not item_lines:
        item_lines = ['- Order items unavailable']

    address = ', '.join(
        part for part in [
            order.street_address,
            order.city,
            order.county,
            order.state,
            order.country,
            order.postal_code,
        ] if part
    )
    lines = [
        f"Hello {order.full_name},",
        status_messages.get(order.status, f"Your order from {store_name} status has been updated."),
        '',
        f"Order ID: ORDER-{order.id:06d}",
        f"Status: {order.get_status_display()}",
        f"Payment: {order.payment_method} ({order.get_payment_status_display()})",
        '',
        "Items:",
        *item_lines,
        '',
        f"Subtotal: Rs. {order.subtotal}",
        f"Delivery fee: Rs. {order.delivery_fee}",
    ]
    if order.discount and order.discount > 0:
        lines.append(f"Discount: Rs. {order.discount}")
    if order.gst_amount and order.gst_amount > 0:
        lines.append(f"GST: Rs. {order.gst_amount}")
    lines.extend([
        f"Total: Rs. {order.total}",
        '',
        f"Delivery address: {address}",
    ])
    if invoice_url:
        lines.extend(['', f"Invoice: {invoice_number}", f"Download invoice: {invoice_url}"])
    if store_phone:
        lines.extend(['', f"For help, contact {store_name}: {store_phone}"])
    lines.append('')
    lines.append('Thank you for shopping with us.')
    return f"https://wa.me/{customer_phone}?text={quote(chr(10).join(lines))}"


@admin_login_required
@require_http_methods(['GET', 'POST'])
def admin_billing(request):
    products = (
        Product.objects
        .filter(status=Product.Status.AVAILABLE, stock__gt=0)
        .select_related('category', 'gst_rate', 'category__gst_rate')
        .prefetch_related('images')
        .order_by('name')
    )
    selected_quantities = {}

    if request.method == 'POST':
        full_name = request.POST.get('full_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        email = request.POST.get('email', '').strip()
        country = request.POST.get('country', 'India').strip() or 'India'
        state = request.POST.get('state', '').strip()
        city = request.POST.get('city', '').strip()
        county = request.POST.get('county', '').strip()
        street_address = request.POST.get('street_address', '').strip()
        postal_code = request.POST.get('postal_code', '').strip()
        payment_method_raw = request.POST.get('payment_method', 'cash').strip().lower()
        upi_id = request.POST.get('upi_id', '').strip()

        required_missing = [
            label for label, value in [
                ('customer name', full_name),
                ('phone number', phone),
                ('state', state),
                ('city', city),
                ('street address', street_address),
                ('postal code', postal_code),
            ] if not value
        ]
        if required_missing:
            messages.error(request, f"Please enter {', '.join(required_missing)}.")
            return redirect('admin_billing')

        if payment_method_raw not in {'cash', 'upi'}:
            messages.error(request, 'Select Cash or UPI as the payment method.')
            return redirect('admin_billing')
        if payment_method_raw == 'upi' and not upi_id:
            messages.error(request, 'UPI ID is required when payment method is UPI.')
            return redirect('admin_billing')

        cart = {}
        for product in products:
            qty_raw = request.POST.get(f'quantity_{product.id}', '').strip()
            if not qty_raw:
                continue
            try:
                qty = int(qty_raw)
            except ValueError:
                messages.error(request, f'Enter a valid quantity for {product.name}.')
                return redirect('admin_billing')
            if qty < 0:
                messages.error(request, f'Quantity for {product.name} cannot be negative.')
                return redirect('admin_billing')
            if qty > 0:
                cart[str(product.id)] = qty
                selected_quantities[product.id] = qty

        if not cart:
            messages.error(request, 'Select at least one product to bill.')
            return redirect('admin_billing')

        delivery_fee_raw = request.POST.get('delivery_fee', '').strip()
        try:
            manual_delivery_fee = money(delivery_fee_raw or 0)
        except (InvalidOperation, ValueError):
            messages.error(request, 'Enter a valid shipping charge.')
            return redirect('admin_billing')
        if manual_delivery_fee < 0:
            messages.error(request, 'Shipping charge cannot be negative.')
            return redirect('admin_billing')

        with transaction.atomic():
            locked_products = {
                product.id: product
                for product in Product.objects.select_for_update().filter(id__in=[int(pid) for pid in cart.keys()])
            }
            for product_id, qty in cart.items():
                product = locked_products.get(int(product_id))
                if not product:
                    messages.error(request, 'One or more selected products could not be found.')
                    return redirect('admin_billing')
                if product.status != Product.Status.AVAILABLE or product.stock < qty:
                    messages.error(request, f'{product.name} has only {product.stock} item(s) available.')
                    return redirect('admin_billing')

            calculation = calculate_order_totals(cart, customer_state=state, shipping=manual_delivery_fee)
            totals = calculation['totals']

            customer = _admin_billing_user(full_name, phone, email)
            payment_method = 'UPI' if payment_method_raw == 'upi' else 'Cash'
            order = Order.objects.create(
                user=customer,
                full_name=full_name,
                phone=phone,
                country=country,
                state=state,
                city=city,
                county=county,
                street_address=street_address,
                postal_code=postal_code,
                subtotal=totals['subtotal'],
                delivery_fee=totals['shipping'],
                cod_extra_charge=money(0),
                discount=money(0),
                taxable_amount=totals['taxable_amount'],
                gst_amount=totals['gst_amount'],
                cgst_amount=totals['cgst_amount'],
                sgst_amount=totals['sgst_amount'],
                igst_amount=totals['igst_amount'],
                tax_type=totals['tax_type'],
                gst_enabled_snapshot=totals['gst_enabled'],
                seller_state_snapshot=totals['seller_state'],
                total=totals['grand_total'],
                status=Order.Status.PROCESSING,
                payment_method=payment_method,
                payment_status=Order.PaymentStatus.PAID,
                payment_id=upi_id if payment_method == 'UPI' else '',
            )

            for item in calculation['items']:
                product = locked_products[item['product'].id]
                qty = item['quantity']
                OrderItem.objects.create(
                    order=order,
                    product=product,
                    product_name=product.name,
                    price=item['unit_price'],
                    quantity=qty,
                    taxable_amount=item['taxable_amount'],
                    gst_percentage=item['gst_percentage'],
                    gst_amount=item['gst_amount'],
                    cgst_amount=item['cgst_amount'],
                    sgst_amount=item['sgst_amount'],
                    igst_amount=item['igst_amount'],
                    hsn_sac_code=item['hsn_sac_code'],
                    final_amount=item['final_amount'],
                )
                product.stock -= qty
                if product.stock <= 0:
                    product.status = Product.Status.OUT_OF_STOCK
                product.save(update_fields=['stock', 'status', 'updated_at'])

        messages.success(request, f'Admin bill created as Order #{order.id}. Payment marked paid by {payment_method}.')
        return redirect(f"{reverse('admin_orders')}?search={order.id}")

    return render(request, 'adminpanel/billing.html', {
        'products': products,
        'selected_quantities': selected_quantities,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'billing',
        'product_count': Product.objects.count(),
        'order_count': _admin_countable_orders().count(),
    })


@admin_login_required
def admin_orders(request):
    base_orders = (
        _admin_visible_orders()
        .prefetch_related('items', 'items__product', 'items__product__images')
        .order_by('-created_at')
    )
    
    status_filter = request.GET.get('status', '').strip()
    search_query = request.GET.get('search', '').strip()

    orders = base_orders
    if search_query:
        if search_query.isdigit():
            orders = orders.filter(id=search_query)
        else:
            orders = orders.filter(full_name__icontains=search_query)

    admin_orders_url = reverse('admin_orders')
    search_suffix = f"&search={quote(search_query)}" if search_query else ''
    all_url = admin_orders_url + (f"?search={quote(search_query)}" if search_query else '')
    status_chips = [{
        'value': '',
        'label': 'All',
        'count': orders.count(),
        'url': all_url,
    }]
    for value, label in _admin_order_status_choices():
        status_chips.append({
            'value': value,
            'label': label,
            'count': orders.filter(status=value).count(),
            'url': f"{admin_orders_url}?status={value}{search_suffix}",
        })

    if status_filter:
        orders = orders.filter(status=status_filter)

    paginator = Paginator(orders, 15)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    orders = list(page_obj.object_list)
    store_address_settings = StoreAddressSettings.current()
    for order in orders:
        order.status_whatsapp_url = _build_order_whatsapp_url(order, store_address_settings, request)
        order.invoice_whatsapp_url = (
            _build_order_whatsapp_url(order, store_address_settings, request, include_invoice=True)
            if order.status == Order.Status.DELIVERED
            else ''
        )

    return render(request, 'adminpanel/orders.html', {
        'orders': orders,
        'page_obj': page_obj,
        'page_range': paginator.get_elided_page_range(page_obj.number, on_each_side=2, on_ends=1),
        'query_without_page': _revenue_querystring(request, page=''),
        'status_filter': status_filter,
        'search_query': search_query,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'orders',
        'order_count': _admin_countable_orders().count(),
        'choices': _admin_order_status_choices(),
        'status_chips': status_chips,
    })


@admin_login_required
def admin_order_notifications(request):
    waiting_orders = _admin_waiting_orders()
    latest_order = waiting_orders.order_by('-id').first()
    waiting_count = waiting_orders.count()

    return JsonResponse({
        'success': True,
        'waiting_count': waiting_count,
        'latest_order_id': latest_order.id if latest_order else 0,
        'latest_order_label': f'#{latest_order.id}' if latest_order else '',
        'latest_order_customer': latest_order.full_name if latest_order else '',
        'latest_order_total': str(latest_order.total) if latest_order else '',
        'latest_order_url': reverse('admin_orders') if latest_order else '',
    })


def _admin_revenue_base_orders():
    return _admin_visible_orders()


def _add_months(value, months):
    month = value.month - 1 + months
    year = value.year + month // 12
    month = month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _parse_admin_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def _revenue_filter_dates(request):
    today = timezone.localdate()
    period = request.GET.get('period', '3m').strip() or '3m'
    start_date = _parse_admin_date(request.GET.get('start_date'))
    end_date = _parse_admin_date(request.GET.get('end_date'))

    if period == 'custom':
        start_date = start_date or _add_months(today, -3)
        end_date = end_date or today
    elif period == 'today':
        start_date = end_date = today
    elif period == '7d':
        start_date = today - timedelta(days=6)
        end_date = today
    elif period == '30d':
        start_date = today - timedelta(days=29)
        end_date = today
    elif period == '6m':
        start_date = _add_months(today, -6)
        end_date = today
    elif period == '12m':
        start_date = _add_months(today, -12)
        end_date = today
    else:
        period = '3m'
        start_date = _add_months(today, -3)
        end_date = today

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, time.max), tz)
    return period, start_date, end_date, start_dt, end_dt


def _money(value):
    return value or Decimal('0.00')


def _revenue_rows(queryset):
    rows = []
    for order in queryset:
        subtotal = _money(order.subtotal)
        delivery_fee = _money(order.delivery_fee)
        cod_extra_charge = _money(order.cod_extra_charge)
        discount = _money(order.discount)
        gst_amount = _money(order.gst_amount)
        total = _money(order.total)
        is_loss = (
            order.status in [Order.Status.CANCELLED, Order.Status.RETURNED]
            or order.payment_status == Order.PaymentStatus.REFUNDED
        )
        rows.append({
            'order': order,
            'gross': subtotal + delivery_fee + cod_extra_charge,
            'discount': discount,
            'tax': gst_amount,
            'net': Decimal('0.00') if is_loss else total,
            'loss': total if is_loss else Decimal('0.00'),
            'result': -total if is_loss else total,
            'is_loss': is_loss,
        })
    return rows


def _revenue_summary(queryset):
    totals = {
        'orders': 0,
        'sales_orders': 0,
        'loss_orders': 0,
        'gross': Decimal('0.00'),
        'discount': Decimal('0.00'),
        'tax': Decimal('0.00'),
        'delivery': Decimal('0.00'),
        'cod': Decimal('0.00'),
        'net': Decimal('0.00'),
        'loss': Decimal('0.00'),
        'profit': Decimal('0.00'),
    }
    for order in queryset:
        subtotal = _money(order.subtotal)
        delivery_fee = _money(order.delivery_fee)
        cod_extra_charge = _money(order.cod_extra_charge)
        discount = _money(order.discount)
        gst_amount = _money(order.gst_amount)
        total = _money(order.total)
        is_loss = (
            order.status in [Order.Status.CANCELLED, Order.Status.RETURNED]
            or order.payment_status == Order.PaymentStatus.REFUNDED
        )

        totals['orders'] += 1
        totals['gross'] += subtotal + delivery_fee + cod_extra_charge
        totals['discount'] += discount
        totals['tax'] += gst_amount
        totals['delivery'] += delivery_fee
        totals['cod'] += cod_extra_charge
        if is_loss:
            totals['loss_orders'] += 1
            totals['loss'] += total
        else:
            totals['sales_orders'] += 1
            totals['net'] += total

    totals['profit'] = totals['net'] - totals['loss']
    return totals


def _revenue_querystring(request, **overrides):
    params = request.GET.copy()
    for key, value in overrides.items():
        if value in [None, '']:
            params.pop(key, None)
        else:
            params[key] = value
    return params.urlencode()


@admin_login_required
def admin_revenue(request):
    period, start_date, end_date, start_dt, end_dt = _revenue_filter_dates(request)
    status_filter = request.GET.get('status', '').strip()
    payment_filter = request.GET.get('payment_status', '').strip()
    search_query = request.GET.get('search', '').strip()

    orders = (
        _admin_revenue_base_orders()
        .select_related('user')
        .prefetch_related('items')
        .filter(created_at__gte=start_dt, created_at__lte=end_dt)
        .order_by('-created_at')
    )
    if status_filter:
        orders = orders.filter(status=status_filter)
    if payment_filter:
        orders = orders.filter(payment_status=payment_filter)
    if search_query:
        if search_query.isdigit():
            orders = orders.filter(id=search_query)
        else:
            orders = orders.filter(Q(full_name__icontains=search_query) | Q(user__email__icontains=search_query))

    summary = _revenue_summary(orders)
    paginator = Paginator(orders, 15)
    page_number = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_number)
    except PageNotAnInteger:
        page_obj = paginator.page(1)
    except EmptyPage:
        page_obj = paginator.page(paginator.num_pages)

    return render(request, 'adminpanel/revenue.html', {
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'revenue',
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'status_filter': status_filter,
        'payment_filter': payment_filter,
        'search_query': search_query,
        'summary': summary,
        'page_obj': page_obj,
        'rows': _revenue_rows(page_obj.object_list),
        'choices': Order.Status.choices,
        'payment_choices': Order.PaymentStatus.choices,
        'query_without_page': _revenue_querystring(request, page=''),
        'pdf_query': _revenue_querystring(request, page=''),
        'order_count': _admin_countable_orders().count(),
    })


def _simple_pdf_bytes(title, lines):
    width = 595
    height = 842
    y = height - 72
    content = ['BT', '/F1 18 Tf', f'56 {y} Td', f'({_pdf_escape(title)}) Tj', 'ET']
    y -= 34
    for raw_line in lines:
        if y < 60:
            break
        content.extend(['BT', '/F1 10 Tf', f'56 {y} Td'])
        for index, line in enumerate(wrap(str(raw_line), width=95) or ['']):
            if index:
                y -= 13
                if y < 60:
                    break
                content.append(f'0 -13 Td')
            content.append(f'({_pdf_escape(line)}) Tj')
        content.append('ET')
        y -= 16

    stream = '\n'.join(content).encode('latin-1', errors='replace')
    objects = [
        b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n',
        b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n',
        b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n',
        b'4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n',
        b'5 0 obj\n<< /Length ' + str(len(stream)).encode('ascii') + b' >>\nstream\n' + stream + b'\nendstream\nendobj\n',
    ]
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_position = len(pdf)
    pdf.extend(f'xref\n0 {len(objects) + 1}\n'.encode('ascii'))
    pdf.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    pdf.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n'.encode('ascii'))
    return bytes(pdf)


@admin_login_required
def download_revenue_pdf(request):
    period, start_date, end_date, start_dt, end_dt = _revenue_filter_dates(request)
    status_filter = request.GET.get('status', '').strip()
    payment_filter = request.GET.get('payment_status', '').strip()
    search_query = request.GET.get('search', '').strip()

    orders = (
        _admin_revenue_base_orders()
        .select_related('user')
        .filter(created_at__gte=start_dt, created_at__lte=end_dt)
        .order_by('-created_at')
    )
    if status_filter:
        orders = orders.filter(status=status_filter)
    if payment_filter:
        orders = orders.filter(payment_status=payment_filter)
    if search_query:
        if search_query.isdigit():
            orders = orders.filter(id=search_query)
        else:
            orders = orders.filter(Q(full_name__icontains=search_query) | Q(user__email__icontains=search_query))

    summary = _revenue_summary(orders)
    lines = [
        f"Period: {start_date:%d %b %Y} to {end_date:%d %b %Y}",
        f"Preset: {period}",
        '',
        f"Total orders: {summary['orders']}",
        f"Successful sale orders: {summary['sales_orders']}",
        f"Loss/refund orders: {summary['loss_orders']}",
        f"Gross amount: Rs. {summary['gross']:.2f}",
        f"Discounts: Rs. {summary['discount']:.2f}",
        f"Tax/GST: Rs. {summary['tax']:.2f}",
        f"Delivery fees: Rs. {summary['delivery']:.2f}",
        f"COD charges: Rs. {summary['cod']:.2f}",
        f"Net revenue: Rs. {summary['net']:.2f}",
        f"Loss/refunds: Rs. {summary['loss']:.2f}",
        f"Profit/Loss result: Rs. {summary['profit']:.2f}",
        '',
        'Orders',
    ]
    for row in _revenue_rows(orders[:35]):
        order = row['order']
        lines.append(
            f"#{order.id} | {timezone.localtime(order.created_at):%d %b %Y} | {order.full_name} | "
            f"{order.get_status_display()} | {order.get_payment_status_display()} | "
            f"Net Rs. {row['net']:.2f} | Loss Rs. {row['loss']:.2f}"
        )
    if orders.count() > 35:
        lines.append(f"... {orders.count() - 35} more orders hidden in PDF preview. Use page filters to narrow the report.")

    response = HttpResponse(
        _simple_pdf_bytes('Revenue Report', lines),
        content_type='application/pdf',
    )
    response['Content-Disposition'] = f'attachment; filename="revenue-{start_date:%Y%m%d}-{end_date:%Y%m%d}.pdf"'
    return response


def _pdf_escape(value):
    return str(value).replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _order_address_pdf_bytes(title, left_sections, right_sections=None):
    width = 595
    height = 842
    content = ['BT', '/F1 22 Tf', f'72 {height - 76} Td', f'({_pdf_escape(title)}) Tj', 'ET']

    def add_sections(sections, x, y, wrap_width):
        for section_title, lines in sections:
            content.extend(['BT', '/F1 13 Tf', f'{x} {y} Td', f'({_pdf_escape(section_title)}) Tj', 'ET'])
            y -= 24
            first_line = True
            content.extend(['BT', '/F1 10 Tf', f'{x} {y} Td'])
            for raw_line in lines:
                wrapped_lines = wrap(str(raw_line), width=wrap_width) or ['']
                for line in wrapped_lines:
                    if not first_line:
                        content.append('0 -15 Td')
                    content.append(f'({_pdf_escape(line)}) Tj')
                    y -= 15
                    first_line = False
                if raw_line == '':
                    content.append('0 -8 Td')
                    y -= 8
            content.append('ET')
            y -= 26
        return y

    add_sections(left_sections, 72, height - 118, 34 if right_sections else 78)
    if right_sections:
        add_sections(right_sections, 315, height - 118, 36)

    stream = '\n'.join(content).encode('latin-1', errors='replace')
    objects = [
        b'1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n',
        b'2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n',
        b'3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>\nendobj\n',
        b'4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n',
        b'5 0 obj\n<< /Length ' + str(len(stream)).encode('ascii') + b' >>\nstream\n' + stream + b'\nendstream\nendobj\n',
    ]
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_position = len(pdf)
    pdf.extend(f'xref\n0 {len(objects) + 1}\n'.encode('ascii'))
    pdf.extend(b'0000000000 65535 f \n')
    for offset in offsets[1:]:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    pdf.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n'.encode('ascii'))
    return bytes(pdf)


@admin_login_required
def download_order_address_pdf(request, pk):
    order = get_object_or_404(Order.objects.select_related('user').prefetch_related('items'), pk=pk)
    store_address_settings = StoreAddressSettings.current()
    store_name = store_address_settings.sender_name.strip() or 'Store'
    from_address = [line.strip() for line in store_address_settings.sender_address.splitlines() if line.strip()]
    if store_address_settings.sender_phone.strip():
        from_address.insert(0, f'Phone: {store_address_settings.sender_phone.strip()}')
    if not from_address:
        from_address = ['Store sender address not configured. Update Store Sender Address in Settings.']

    customer_address = [
        order.full_name,
        f'Phone: {order.phone}',
        order.street_address,
        ', '.join(part for part in [order.city, order.county, order.state] if part),
        ', '.join(part for part in [order.country, order.postal_code] if part),
    ]

    shipping_charge = 'Free' if money(order.delivery_fee) == 0 else f'Rs. {money(order.delivery_fee)}'
    product_lines = []
    for index, item in enumerate(order.items.all(), start=1):
        product_lines.extend([
            f'{index}. {item.product_name}',
            f'Qty: {item.quantity}',
            '',
        ])
    if not product_lines:
        product_lines = ['No product details available.']

    left_sections = [
        ('From Address', from_address),
        ('Customer Address', [line for line in customer_address if line]),
    ]
    right_sections = [
        ('Order Details', [
            f'Order ID: ORDER-{order.id:06d}',
            f'Order Date: {timezone.localtime(order.created_at).strftime("%d %b %Y, %I:%M %p")}',
            f'Payment Method: {order.payment_method}',
            f'Payment Status: {order.get_payment_status_display()}',
        ]),
        ('Product Details', product_lines),
        ('Amount Details', [
            f'Shipping Charge: {shipping_charge}',
            f'Final Amount: Rs. {money(order.total)}',
        ]),
    ]
    response = HttpResponse(
        _order_address_pdf_bytes(store_name, left_sections, right_sections),
        content_type='application/pdf',
    )
    response['Content-Disposition'] = f'attachment; filename="order-{order.id}-address.pdf"'
    return response


def _apply_order_status_change(order, new_status):
    old_status = order.status
    if new_status == old_status:
        return False
    if (
        order.payment_method.lower() == 'phonepe'
        and order.payment_status == Order.PaymentStatus.PAID
        and new_status == Order.Status.PENDING
    ):
        return False
    if (
        order.payment_method.lower() == 'phonepe'
        and order.payment_status != Order.PaymentStatus.PAID
        and new_status not in [Order.Status.PENDING, Order.Status.CANCELLED]
    ):
        return False

    order.status = new_status
    if new_status == Order.Status.DELIVERED and not order.delivered_at:
        order.delivered_at = timezone.now()
    elif new_status == Order.Status.CANCELLED:
        if not order.cancelled_at:
            order.cancelled_at = timezone.now()
        order.cancel_requested = True
        for item in order.items.all():
            if item.product:
                item.product.stock += item.quantity
                if item.product.status == Product.Status.OUT_OF_STOCK and item.product.stock > 0:
                    item.product.status = Product.Status.AVAILABLE
                item.product.save()
    elif new_status in [Order.Status.RETURN_REQUESTED, Order.Status.RETURNED]:
        if not order.returned_at:
            order.returned_at = timezone.now()
        order.return_requested = True
    order.save()

    status_event_map = {
        Order.Status.SHIPPED: 'shipped',
        Order.Status.DELIVERED: 'delivered',
        Order.Status.CANCELLED: 'cancelled',
        Order.Status.RETURN_REQUESTED: 'replacement_requested',
        Order.Status.RETURNED: 'replacement_completed',
    }
    event_type = status_event_map.get(new_status)
    if event_type:
        send_order_status_email(order, event_type)
    return True


@admin_login_required
@require_POST
def bulk_update_order_status(request):
    order_ids = request.POST.getlist('order_ids')
    new_status = request.POST.get('status', '').strip()
    redirect_to = request.META.get('HTTP_REFERER') or 'admin_orders'

    if not order_ids:
        messages.error(request, 'Select at least one order.')
        return redirect(redirect_to)

    if new_status not in dict(Order.Status.choices):
        messages.error(request, 'Invalid status selected.')
        return redirect(redirect_to)

    orders = list(Order.objects.filter(pk__in=order_ids).prefetch_related('items', 'items__product'))
    if len(orders) != len(set(order_ids)):
        messages.error(request, 'One or more selected orders could not be found.')
        return redirect(redirect_to)

    current_statuses = {order.status for order in orders}
    if len(current_statuses) != 1:
        messages.error(request, 'Bulk status change is allowed only when all selected orders have the same current status.')
        return redirect(redirect_to)

    if Order.Status.CANCELLED in current_statuses:
        messages.error(request, 'Cancelled orders are locked and cannot be changed.')
        return redirect(redirect_to)

    paid_online_to_pending = [
        order for order in orders
        if order.payment_method.lower() == 'phonepe'
        and order.payment_status == Order.PaymentStatus.PAID
        and new_status == Order.Status.PENDING
    ]
    if paid_online_to_pending:
        messages.error(request, 'Paid online orders cannot be changed back to Pending Payment.')
        return redirect(redirect_to)

    blocked_unpaid_online = [
        order for order in orders
        if order.payment_method.lower() == 'phonepe'
        and order.payment_status != Order.PaymentStatus.PAID
        and new_status not in [Order.Status.PENDING, Order.Status.CANCELLED]
    ]
    if blocked_unpaid_online:
        messages.error(request, 'Unpaid PhonePe orders cannot be moved forward before verified payment success.')
        return redirect(redirect_to)

    updated_count = 0
    for order in orders:
        if _apply_order_status_change(order, new_status):
            updated_count += 1

    if updated_count:
        messages.success(request, f'{updated_count} selected orders updated to {_admin_order_status_label(new_status)}.')
    else:
        messages.info(request, 'Selected orders already have that status.')
    return redirect(redirect_to)


@admin_login_required
@require_POST
def update_order_status(request, pk):
    order = get_object_or_404(Order, pk=pk)
    new_status = request.POST.get('status', '').strip()
    old_status = order.status
    
    # Once an order is cancelled, disallow status updates
    if order.status == Order.Status.CANCELLED:
        messages.warning(request, f"Order #{order.id} is already cancelled and cannot be changed.")
        return redirect('admin_orders')

    if (
        order.payment_method.lower() == 'phonepe'
        and order.payment_status == Order.PaymentStatus.PAID
        and new_status == Order.Status.PENDING
    ):
        messages.error(request, f"Order #{order.id} is already paid online and cannot be changed back to Pending Payment.")
        return redirect('admin_orders')

    if (
        order.payment_method.lower() == 'phonepe'
        and order.payment_status != Order.PaymentStatus.PAID
        and new_status not in [Order.Status.PENDING, Order.Status.CANCELLED]
    ):
        messages.error(request, f"Order #{order.id} is waiting for PhonePe payment verification and cannot be processed yet.")
        return redirect('admin_orders')

    if new_status in dict(Order.Status.choices):
        if new_status != old_status:
            updated = _apply_order_status_change(order, new_status)
            messages.success(request, f"Order #{order.id} status updated to '{_admin_order_status_label(order.status)}'.")
            if updated and new_status != Order.Status.PROCESSING:
                whatsapp_url = _build_order_whatsapp_url(
                    order,
                    StoreAddressSettings.current(),
                    request,
                    include_invoice=(new_status == Order.Status.DELIVERED),
                )
                if whatsapp_url:
                    return redirect(whatsapp_url)
                messages.warning(request, f"Order #{order.id} was updated, but no valid customer WhatsApp number is available.")
        else:
            messages.info(request, f"Order #{order.id} status is already '{order.get_status_display()}'.")
    else:
        messages.error(request, "Invalid status transition selected.")
        
    return redirect('admin_orders')


@admin_login_required
def admin_reviews(request):
    from .models import Review
    search = request.GET.get('search', '').strip()
    reviews = Review.objects.select_related('user', 'product').order_by('-created_at')
    
    if search:
        reviews = reviews.filter(
            Q(product__name__icontains=search) | 
            Q(user__email__icontains=search) |
            Q(comment__icontains=search)
        )
        
    return render(request, 'adminpanel/reviews.html', {
        'reviews': reviews,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'reviews',
        'filter_search': search,
    })


@admin_login_required
@require_POST
def delete_review(request, pk):
    from .models import Review
    review = get_object_or_404(Review, pk=pk)
    product_name = review.product.name
    user_email = review.user.email
    review.delete()
    messages.success(request, f'Review by {user_email} on {product_name} deleted successfully.')
    return redirect('admin_reviews')


# ─── Contact Messages Views ───────────────────────────────────

@admin_login_required
def admin_messages(request):
    search = request.GET.get('search', '').strip()
    contact_messages = ContactMessage.objects.all().order_by('-created_at')

    if search:
        contact_messages = contact_messages.filter(
            Q(name__icontains=search) |
            Q(email__icontains=search) |
            Q(subject__icontains=search) |
            Q(message__icontains=search)
        )

    unread_count = ContactMessage.objects.filter(is_read=False).count()

    return render(request, 'adminpanel/messages.html', {
        'contact_messages': contact_messages,
        'unread_count': unread_count,
        'message_count': ContactMessage.objects.count(),
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'messages',
        'filter_search': search,
    })


@admin_login_required
@require_POST
def delete_message(request, pk):
    msg = get_object_or_404(ContactMessage, pk=pk)
    sender_name = msg.name
    msg.delete()
    messages.success(request, f'Message from {sender_name} deleted successfully.')
    return redirect('admin_messages')


@admin_login_required
@require_POST
def toggle_message_read(request, pk):
    msg = get_object_or_404(ContactMessage, pk=pk)
    msg.is_read = not msg.is_read
    msg.save(update_fields=['is_read'])
    status_str = "marked as read" if msg.is_read else "marked as unread"
    messages.success(request, f'Message from {msg.name} {status_str}.')
    return redirect('admin_messages')


# ─── Coupon Management Views ─────────────────────────────────

@admin_login_required
def admin_coupons(request):
    search = request.GET.get('search', '').strip()
    status_filter = request.GET.get('status', '').strip()
    
    coupons = Coupon.objects.all().order_by('-created_at')
    
    if search:
        coupons = coupons.filter(code__icontains=search)
        
    now = timezone.now()
    if status_filter == 'active':
        coupons = coupons.filter(is_active=True, valid_from__lte=now, valid_until__gte=now)
    elif status_filter == 'scheduled':
        coupons = coupons.filter(is_active=True, valid_from__gt=now)
    elif status_filter == 'inactive':
        coupons = coupons.filter(Q(is_active=False) | Q(valid_until__lt=now))
    elif status_filter == 'expired':
        coupons = coupons.filter(valid_until__lt=now)

    total_coupons = Coupon.objects.count()
    active_coupons = Coupon.objects.filter(is_active=True, valid_from__lte=now, valid_until__gte=now).count()
    expired_coupons = Coupon.objects.filter(valid_until__lt=now).count()

    return render(request, 'adminpanel/coupons.html', {
        'coupons': coupons,
        'total_coupons': total_coupons,
        'active_coupons': active_coupons,
        'expired_coupons': expired_coupons,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'coupons',
        'filter_search': search,
        'status_filter': status_filter,
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def add_coupon(request):
    now = timezone.now()
    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        discount_type = request.POST.get('discount_type', Coupon.DiscountType.PERCENTAGE)
        
        try:
            discount_value = float(request.POST.get('discount_value', 0))
            min_purchase = float(request.POST.get('min_purchase', 0.0))
        except ValueError:
            messages.error(request, 'Please enter valid numeric values for discount and minimum purchase.')
            return redirect('add_coupon')

        max_discount_raw = request.POST.get('max_discount', '').strip()
        max_discount = float(max_discount_raw) if max_discount_raw else None

        usage_limit_raw = request.POST.get('usage_limit', '').strip()
        usage_limit = int(usage_limit_raw) if usage_limit_raw else None

        valid_from_raw = request.POST.get('valid_from', '').strip()
        days_to_expire_raw = request.POST.get('days_to_expire', '').strip()
        valid_until_raw = request.POST.get('valid_until', '').strip()
        
        if not code:
            messages.error(request, 'Coupon code is required.')
            return redirect('add_coupon')

        if Coupon.objects.filter(code__iexact=code).exists():
            messages.error(request, f"Coupon code '{code}' already exists. Please choose a unique code.")
            return redirect('add_coupon')

        # Parse valid_from (Start Date)
        if valid_from_raw:
            try:
                from django.utils.dateparse import parse_datetime
                parsed_from = parse_datetime(valid_from_raw)
                if not parsed_from:
                    from datetime import datetime
                    parsed_from = timezone.make_aware(datetime.strptime(valid_from_raw, '%Y-%m-%d'))
                valid_from = parsed_from
            except Exception:
                valid_from = now
        else:
            valid_from = now

        # Compute valid_until (Expiry Date)
        if days_to_expire_raw and days_to_expire_raw.isdigit():
            valid_until = valid_from + timedelta(days=int(days_to_expire_raw))
        elif valid_until_raw:
            try:
                from django.utils.dateparse import parse_datetime
                parsed_until = parse_datetime(valid_until_raw)
                if not parsed_until:
                    from datetime import datetime
                    parsed_until = timezone.make_aware(datetime.strptime(valid_until_raw, '%Y-%m-%d'))
                valid_until = parsed_until
            except Exception:
                valid_until = valid_from + timedelta(days=30)
        else:
            valid_until = valid_from + timedelta(days=30)

        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == '1'

        Coupon.objects.create(
            code=code,
            discount_type=discount_type,
            discount_value=discount_value,
            min_purchase=min_purchase,
            max_discount=max_discount,
            valid_from=valid_from,
            valid_until=valid_until,
            usage_limit=usage_limit,
            is_active=is_active,
        )

        messages.success(request, f"Coupon code '{code}' created successfully!")
        return redirect('admin_coupons')

    return render(request, 'adminpanel/coupon_form.html', {
        'form_title': 'Add New Coupon',
        'submit_text': 'Create Coupon',
        'discount_types': Coupon.DiscountType.choices,
        'valid_from_formatted': now.strftime('%Y-%m-%dT%H:%M'),
        'valid_until_formatted': (now + timedelta(days=7)).strftime('%Y-%m-%dT%H:%M'),
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'coupons',
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def edit_coupon(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)

    if request.method == 'POST':
        code = request.POST.get('code', '').strip().upper()
        discount_type = request.POST.get('discount_type', coupon.discount_type)
        
        try:
            discount_value = float(request.POST.get('discount_value', coupon.discount_value))
            min_purchase = float(request.POST.get('min_purchase', coupon.min_purchase))
        except ValueError:
            messages.error(request, 'Please enter valid numeric values.')
            return redirect('edit_coupon', pk=pk)

        max_discount_raw = request.POST.get('max_discount', '').strip()
        max_discount = float(max_discount_raw) if max_discount_raw else None

        usage_limit_raw = request.POST.get('usage_limit', '').strip()
        usage_limit = int(usage_limit_raw) if usage_limit_raw else None

        valid_from_raw = request.POST.get('valid_from', '').strip()
        days_to_expire_raw = request.POST.get('days_to_expire', '').strip()
        valid_until_raw = request.POST.get('valid_until', '').strip()

        if not code:
            messages.error(request, 'Coupon code cannot be empty.')
            return redirect('edit_coupon', pk=pk)

        if Coupon.objects.filter(code__iexact=code).exclude(pk=pk).exists():
            messages.error(request, f"Coupon code '{code}' is already used by another coupon.")
            return redirect('edit_coupon', pk=pk)

        coupon.code = code
        coupon.discount_type = discount_type
        coupon.discount_value = discount_value
        coupon.min_purchase = min_purchase
        coupon.max_discount = max_discount
        coupon.usage_limit = usage_limit
        coupon.is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == '1'

        if valid_from_raw:
            try:
                from django.utils.dateparse import parse_datetime
                parsed_from = parse_datetime(valid_from_raw)
                if parsed_from:
                    coupon.valid_from = parsed_from
            except Exception:
                pass

        if days_to_expire_raw and days_to_expire_raw.isdigit():
            coupon.valid_until = coupon.valid_from + timedelta(days=int(days_to_expire_raw))
        elif valid_until_raw:
            try:
                from django.utils.dateparse import parse_datetime
                parsed = parse_datetime(valid_until_raw)
                if parsed:
                    coupon.valid_until = parsed
            except Exception:
                pass

        coupon.save()
        messages.success(request, f"Coupon '{coupon.code}' updated successfully!")
        return redirect('admin_coupons')

    return render(request, 'adminpanel/coupon_form.html', {
        'coupon': coupon,
        'form_title': f"Edit Coupon: {coupon.code}",
        'submit_text': 'Save Changes',
        'discount_types': Coupon.DiscountType.choices,
        'valid_from_formatted': coupon.valid_from.strftime('%Y-%m-%dT%H:%M') if coupon.valid_from else '',
        'valid_until_formatted': coupon.valid_until.strftime('%Y-%m-%dT%H:%M') if coupon.valid_until else '',
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'coupons',
    })


@admin_login_required
@require_POST
def delete_coupon(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    code = coupon.code
    coupon.delete()
    messages.success(request, f"Coupon '{code}' deleted successfully.")
    return redirect('admin_coupons')


@admin_login_required
@require_POST
def toggle_coupon(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)
    coupon.is_active = not coupon.is_active
    coupon.save(update_fields=['is_active'])
    status_str = "activated" if coupon.is_active else "deactivated"
    messages.success(request, f"Coupon '{coupon.code}' {status_str}.")
    return redirect('admin_coupons')


# ─── Popup Banner Management Views ────────────────────────────

@admin_login_required
def admin_popups(request):
    search = request.GET.get('search', '').strip()
    popups = PopupBanner.objects.all().order_by('-created_at')

    if search:
        popups = popups.filter(Q(title__icontains=search) | Q(subtitle__icontains=search))

    popup_count = PopupBanner.objects.count()
    active_popup_count = PopupBanner.objects.filter(is_active=True).count()

    return render(request, 'adminpanel/popups.html', {
        'popups': popups,
        'popup_count': popup_count,
        'active_popup_count': active_popup_count,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'popups',
        'filter_search': search,
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def add_popup(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        subtitle = request.POST.get('subtitle', '').strip()
        button_text = request.POST.get('button_text', 'Shop Now').strip() or 'Shop Now'
        link_url = request.POST.get('link_url', '/shop/').strip() or '/shop/'
        
        try:
            display_delay = int(request.POST.get('display_delay', 1))
        except (ValueError, TypeError):
            display_delay = 1

        frequency = request.POST.get('frequency', PopupBanner.DisplayFrequency.ONCE_PER_SESSION)
        is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == '1'
        show_on_homepage = request.POST.get('show_on_homepage') == 'on' or request.POST.get('show_on_homepage') == '1'
        image = request.FILES.get('image')

        if not image:
            messages.error(request, 'Popup image is required.')
            return redirect('add_popup')

        PopupBanner.objects.create(
            title=title,
            subtitle=subtitle,
            image=image,
            button_text=button_text,
            link_url=link_url,
            display_delay=display_delay,
            frequency=frequency,
            is_active=is_active,
            show_on_homepage=show_on_homepage,
        )

        messages.success(request, 'Popup Banner created successfully!')
        return redirect('admin_popups')

    categories = Category.objects.filter(status=Category.Status.ACTIVE).order_by('name')
    products = Product.objects.filter(status=Product.Status.AVAILABLE).order_by('name')

    return render(request, 'adminpanel/popup_form.html', {
        'form_title': 'Add New Popup Banner',
        'submit_text': 'Create Popup Banner',
        'frequencies': PopupBanner.DisplayFrequency.choices,
        'categories': categories,
        'products': products,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'popups',
    })


@admin_login_required
@require_http_methods(['GET', 'POST'])
def edit_popup(request, pk):
    popup = get_object_or_404(PopupBanner, pk=pk)

    if request.method == 'POST':
        popup.title = request.POST.get('title', '').strip()
        popup.subtitle = request.POST.get('subtitle', '').strip()
        popup.button_text = request.POST.get('button_text', 'Shop Now').strip() or 'Shop Now'
        popup.link_url = request.POST.get('link_url', '/shop/').strip() or '/shop/'

        try:
            popup.display_delay = int(request.POST.get('display_delay', popup.display_delay))
        except (ValueError, TypeError):
            pass

        popup.frequency = request.POST.get('frequency', popup.frequency)
        popup.is_active = request.POST.get('is_active') == 'on' or request.POST.get('is_active') == '1'
        popup.show_on_homepage = request.POST.get('show_on_homepage') == 'on' or request.POST.get('show_on_homepage') == '1'

        new_image = request.FILES.get('image')
        if new_image:
            if popup.image:
                popup.image.delete(save=False)
            popup.image = new_image

        popup.save()
        messages.success(request, f"Popup Banner '{popup}' updated successfully!")
        return redirect('admin_popups')

    categories = Category.objects.filter(status=Category.Status.ACTIVE).order_by('name')
    products = Product.objects.filter(status=Product.Status.AVAILABLE).order_by('name')

    return render(request, 'adminpanel/popup_form.html', {
        'popup': popup,
        'form_title': f"Edit Popup Banner #{popup.id}",
        'submit_text': 'Save Changes',
        'frequencies': PopupBanner.DisplayFrequency.choices,
        'categories': categories,
        'products': products,
        'admin_name': request.session.get('admin_name'),
        'active_nav': 'popups',
    })


@admin_login_required
@require_POST
def delete_popup(request, pk):
    popup = get_object_or_404(PopupBanner, pk=pk)
    if popup.image:
        popup.image.delete(save=False)
    popup.delete()
    messages.success(request, 'Popup Banner deleted successfully.')
    return redirect('admin_popups')


@admin_login_required
@require_POST
def toggle_popup(request, pk):
    popup = get_object_or_404(PopupBanner, pk=pk)
    popup.is_active = not popup.is_active
    popup.save(update_fields=['is_active'])
    status_str = "activated" if popup.is_active else "deactivated"
    messages.success(request, f"Popup Banner {status_str}.")
    return redirect('admin_popups')




