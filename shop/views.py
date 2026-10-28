from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
import base64
import hashlib
import secrets
import uuid
import json
import random
import re
import requests
import string

from django.core import signing
from django.core.signing import BadSignature, SignatureExpired
from .forms import NewsletterSubscriptionForm
from .models import CarouselItem, Category, CategoryTag, Product, PasswordResetOTP, Favorite, DeliveryZone, DeliveryConfig, Order, OrderItem, PaymentTransaction, PaymentWebhookEvent, StoreAddressSettings, UserProfile, Review, OTPSettings, ContactMessage, GSTConfig, HomeNewsTicker
from .decorators import login_required_ajax
from .email_utils import send_order_status_email
from .tax import calculate_cart_totals, calculate_order_totals, calculate_shipping, money



def home(request):
    carousel_items = CarouselItem.objects.filter(is_active=True).select_related('product')
    news_tickers = HomeNewsTicker.objects.filter(is_active=True).order_by('display_order', 'id')
    return render(request, 'shop/home.html', {
        'carousel_items': carousel_items,
        'news_tickers': news_tickers,
    })


def shop_all(request):
    return render(request, 'shop/shop.html')


def categories_all(request):
    categories = (
        Category.objects.filter(status=Category.Status.ACTIVE)
        .prefetch_related('tags')
        .order_by('name')
    )
    tags = CategoryTag.objects.filter(is_active=True, categories__status=Category.Status.ACTIVE).distinct()
    return render(request, 'shop/categories.html', {
        'categories': categories,
        'tags': tags,
    })


def about_page(request):
    return render(request, 'shop/about.html')


def loading_demo(request):
    return render(request, 'shop/loading_demo.html')


@require_POST
def send_contact_message(request):
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()

        if not name or not email or not message:
            return JsonResponse({'success': False, 'message': 'Please fill in all required fields.'}, status=400)

        ContactMessage.objects.create(
            name=name,
            email=email,
            subject=subject or 'General Inquiry',
            message=message
        )
        return JsonResponse({'success': True, 'message': 'Thank you! Your message has been received and recorded.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': 'An error occurred while saving your message.'}, status=500)


def product_detail(request, slug):
    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('images'),
        slug=slug,
    )
    cart_qty = 0
    is_favorited = False
    if request.user.is_authenticated:
        cart = request.session.get('cart', {})
        if isinstance(cart, dict):
            try:
                cart_qty = int(cart.get(str(product.id), 0))
            except (ValueError, TypeError):
                cart_qty = 0
        is_favorited = Favorite.objects.filter(user=request.user, product=product).exists()

    reviews = product.reviews.select_related('user').order_by('-created_at')

    # Fetch random suggested products excluding current product
    suggested_products = list(
        Product.objects.filter(status=Product.Status.AVAILABLE)
        .exclude(id=product.id)
        .select_related('category')
        .prefetch_related('images', 'reviews')
        .order_by('?')[:4]
    )

    if request.user.is_authenticated:
        fav_ids = set(
            Favorite.objects.filter(user=request.user, product__in=suggested_products)
            .values_list('product_id', flat=True)
        )
        for p in suggested_products:
            p.is_user_favorited = p.id in fav_ids
    else:
        for p in suggested_products:
            p.is_user_favorited = False

    return render(request, 'shop/product_detail.html', {
        'product': product,
        'cart_qty': cart_qty,
        'is_favorited': is_favorited,
        'reviews': reviews,
        'suggested_products': suggested_products,
    })


@require_POST
@login_required_ajax
def add_product_review(request, slug):
    try:
        data = json.loads(request.body)
        rating = int(data.get('rating', 5))
        comment = data.get('comment', '').strip()
    except (json.JSONDecodeError, AttributeError, ValueError):
        rating = int(request.POST.get('rating', 5))
        comment = request.POST.get('comment', '').strip()

    product = get_object_or_404(Product, slug=slug)

    # Validate rating
    if rating < 1 or rating > 5:
        return JsonResponse({
            'success': False,
            'message': 'Rating must be between 1 and 5 stars.'
        }, status=400)

    # Check if user already reviewed
    review, created = Review.objects.get_or_create(
        user=request.user,
        product=product,
        defaults={'rating': rating, 'comment': comment}
    )

    if not created:
        review.rating = rating
        review.comment = comment
        review.save()
        message = 'Your review has been updated!'
    else:
        message = 'Your review has been added successfully!'

    return JsonResponse({
        'success': True,
        'message': message,
        'average_rating': product.average_rating,
        'review_count': product.review_count
    })


@require_POST
def newsletter_subscribe(request):
    form = NewsletterSubscriptionForm(request.POST)
    if form.is_valid():
        form.save()
        return JsonResponse({
            'success': True,
            'message': 'Thanks for subscribing. You are on the list.',
        }, status=201)

    error = form.errors.get('email', ['Enter a valid email address.'])[0]
    return JsonResponse({
        'success': False,
        'message': error,
        'errors': form.errors,
    }, status=400)


def login_redirect_view(request):
    next_url = request.GET.get('next', '/')
    if request.user.is_authenticated:
        return redirect(next_url)
    return redirect(f'/?login_required=1&next={next_url}')


# --- AJAX Authentication Views ---

@require_POST
def ajax_login(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
        password = data.get('password', '')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON request.'}, status=400)

    if not email or not password:
        return JsonResponse({'success': False, 'message': 'Email and password are required.'}, status=400)

    user = authenticate(request, username=email, password=password)
    if user is not None:
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return JsonResponse({'success': True, 'message': 'Logged in successfully.'})
    else:
        return JsonResponse({'success': False, 'message': 'Invalid email or password.'}, status=400)


def validate_strong_unique_password(password, user=None):
    """
    Enforces strong and unique password requirements:
    1. Minimum 8 characters.
    2. Must contain at least one uppercase letter (A-Z).
    3. Must contain at least one lowercase letter (a-z).
    4. Must contain at least one numeric digit (0-9).
    5. Must contain at least one special character (e.g. @, #, $, %, !).
    6. Uniqueness: Cannot be identical to current/previous password.
    """
    if not password:
        return False, "Password cannot be empty."
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r'[A-Z]', password):
        return False, "Password must contain at least one uppercase letter (A-Z)."
    if not re.search(r'[a-z]', password):
        return False, "Password must contain at least one lowercase letter (a-z)."
    if not re.search(r'\d', password):
        return False, "Password must contain at least one numeric digit (0-9)."
    if not re.search(r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]', password):
        return False, "Password must contain at least one special character (e.g. @, #, $, %, !)."

    if user and user.check_password(password):
        return False, "New password cannot be the same as your current/previous password. Please choose a unique password."

    return True, ""


@require_POST
def ajax_signup(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
        password = data.get('password', '')
        confirm_password = data.get('confirm_password', '')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON request.'}, status=400)

    if not email or not password or not confirm_password:
        return JsonResponse({'success': False, 'message': 'All fields are required.'}, status=400)

    # Basic validations
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        return JsonResponse({'success': False, 'message': 'Please enter a valid email address.'}, status=400)

    if password != confirm_password:
        return JsonResponse({'success': False, 'message': 'Passwords do not match.'}, status=400)

    # Strong password validation
    is_valid_pw, pw_msg = validate_strong_unique_password(password)
    if not is_valid_pw:
        return JsonResponse({'success': False, 'message': pw_msg}, status=400)

    if User.objects.filter(username=email).exists() or User.objects.filter(email=email).exists():
        return JsonResponse({'success': False, 'message': 'An account with this email already exists.'}, status=400)

    try:
        user = User.objects.create_user(username=email, email=email)
        user.set_password(password)
        user.save()
        UserProfile.objects.get_or_create(user=user)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return JsonResponse({'success': True, 'message': 'Account created and logged in successfully.'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': f'Signup failed: {str(e)}'}, status=500)


@require_POST
def ajax_logout(request):
    logout(request)
    return JsonResponse({'success': True, 'message': 'Logged out successfully.'})


# --- OTP Forgot Password Views ---

@require_POST
def request_otp(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON request.'}, status=400)

    if not email:
        return JsonResponse({'success': False, 'message': 'Email is required.'}, status=400)

    user = User.objects.filter(email=email).first()
    if not user:
        return JsonResponse({'success': False, 'message': 'No account found with this email address.'}, status=400)

    otp_config = OTPSettings.current()
    now = timezone.now()
    cooldown_time = now - timedelta(seconds=otp_config.resend_cooldown_seconds)
    ip = request.META.get('REMOTE_ADDR')

    # Rate limiting based on admin configured cooldown
    recent_otp = PasswordResetOTP.objects.filter(user=user, created_at__gte=cooldown_time).order_by('-created_at').first()
    if recent_otp:
        elapsed = (now - recent_otp.created_at).total_seconds()
        remaining = max(1, int(otp_config.resend_cooldown_seconds - elapsed))
        return JsonResponse({
            'success': False, 
            'message': f'Please wait {remaining} seconds before requesting another OTP.',
            'remaining_cooldown': remaining
        }, status=429)

    # Invalidate previous unused OTPs for this user
    PasswordResetOTP.objects.filter(user=user, is_used=False).update(is_used=True)

    # Generate OTP code based on admin configured length (4, 6, or 8 digits)
    otp_code = ''.join(random.choices(string.digits, k=otp_config.otp_length))
    expires_at = now + timedelta(minutes=otp_config.otp_expiry_minutes)

    PasswordResetOTP.objects.create(
        user=user,
        otp_code=otp_code,
        ip_address=ip,
        expires_at=expires_at
    )

    # Render HTML email with company name and rich layout
    from django.conf import settings
    from django.core.mail import get_connection, EmailMultiAlternatives
    from django.template.loader import render_to_string

    company = otp_config.company_name or 'Kj Apperals'
    store_settings = StoreAddressSettings.current()
    brand_logo_url = ''
    if store_settings.brand_logo:
        brand_logo_url = request.build_absolute_uri(store_settings.brand_logo.url)
    smtp_user = (otp_config.smtp_host_user or getattr(settings, 'EMAIL_HOST_USER', '')).strip()
    smtp_pass = (otp_config.smtp_host_password or getattr(settings, 'EMAIL_HOST_PASSWORD', '')).strip()
    sender_addr = smtp_user if smtp_user else (otp_config.sender_email or 'webmaster@localhost')
    formatted_from = f"{company} Security <{sender_addr}>"

    subject = f"{company} - Your Password Reset Code: {otp_code}"
    text_content = (
        f"Hello,\n\n"
        f"Your {company} verification code to reset your password is: {otp_code}\n\n"
        f"This code will expire in {otp_config.otp_expiry_minutes} minutes.\n"
        f"If you did not request a password reset, please ignore this email."
    )

    html_content = render_to_string('emails/otp_email.html', {
        'company_name': company,
        'brand_logo_url': brand_logo_url,
        'otp_code': otp_code,
        'expiry_minutes': otp_config.otp_expiry_minutes,
    })

    try:
        if smtp_user and smtp_pass:
            connection = get_connection(
                backend='django.core.mail.backends.smtp.EmailBackend',
                host=getattr(settings, 'EMAIL_HOST', 'smtp.gmail.com'),
                port=getattr(settings, 'EMAIL_PORT', 587),
                username=smtp_user,
                password=smtp_pass,
                use_tls=getattr(settings, 'EMAIL_USE_TLS', True),
            )
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=formatted_from,
                to=[email],
                connection=connection,
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
        else:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=formatted_from,
                to=[email],
            )
            msg.attach_alternative(html_content, "text/html")
            msg.send(fail_silently=False)
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send OTP email to {email}: {e}")
        return JsonResponse({'success': False, 'message': f'Failed to send email to {email}: {str(e)}'}, status=500)

    return JsonResponse({
        'success': True,
        'message': f'A {otp_config.otp_length}-digit OTP code has been sent to your email.',
        'otp_length': otp_config.otp_length,
        'expiry_minutes': otp_config.otp_expiry_minutes,
        'cooldown_seconds': otp_config.resend_cooldown_seconds,
    })


@require_POST
def verify_otp(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
        otp_code = data.get('otp_code', '').strip()
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON request.'}, status=400)

    if not email or not otp_code:
        return JsonResponse({'success': False, 'message': 'Email and OTP code are required.'}, status=400)

    otp_config = OTPSettings.current()
    if len(otp_code) != otp_config.otp_length:
        return JsonResponse({'success': False, 'message': f'Please enter a valid {otp_config.otp_length}-digit OTP code.'}, status=400)

    now = timezone.now()
    otp_record = PasswordResetOTP.objects.filter(
        user__email=email,
        otp_code=otp_code,
        is_used=False
    ).order_by('-created_at').first()

    if not otp_record:
        return JsonResponse({'success': False, 'message': 'Invalid OTP code. Please check and try again.'}, status=400)

    if otp_record.expires_at <= now:
        otp_record.is_used = True
        otp_record.save()
        return JsonResponse({'success': False, 'message': 'This OTP code has expired. Please request a new code.'}, status=400)

    otp_record.is_used = True
    otp_record.save()

    return JsonResponse({'success': True, 'message': 'OTP code verified successfully. Please set your new password.'})


@require_POST
def reset_password(request):
    try:
        data = json.loads(request.body)
        email = data.get('email', '').strip()
        otp_code = data.get('otp_code', '').strip()
        password = data.get('password', '')
        confirm_password = data.get('confirm_password', '')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid JSON request.'}, status=400)

    if not email or not otp_code or not password or not confirm_password:
        return JsonResponse({'success': False, 'message': 'All fields are required.'}, status=400)

    if password != confirm_password:
        return JsonResponse({'success': False, 'message': 'Passwords do not match.'}, status=400)

    otp_config = OTPSettings.current()
    now = timezone.now()
    cutoff_time = now - timedelta(minutes=otp_config.otp_expiry_minutes + 15)

    otp_record = PasswordResetOTP.objects.filter(
        user__email=email,
        otp_code=otp_code,
        is_used=True,
        created_at__gte=cutoff_time
    ).first()

    if not otp_record:
        return JsonResponse({'success': False, 'message': 'Verification session expired or invalid. Please request a new OTP.'}, status=400)

    user = otp_record.user

    # Validate strong and unique password (cannot reuse previous password)
    is_valid_pw, pw_msg = validate_strong_unique_password(password, user=user)
    if not is_valid_pw:
        return JsonResponse({'success': False, 'message': pw_msg}, status=400)

    user.set_password(password)
    user.save()

    # Clear all OTPs for this user
    PasswordResetOTP.objects.filter(user=user).delete()

    return JsonResponse({'success': True, 'message': 'Password reset successful. You can now log in.'})


# --- AJAX protected e-commerce actions ---

@require_POST
@login_required_ajax
def add_to_cart(request):
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
    except (json.JSONDecodeError, AttributeError, ValueError):
        product_id = request.POST.get('product_id')

    product = get_object_or_404(Product, id=product_id)
    cart = request.session.get('cart', {})
    product_id_str = str(product.id)
    cart[product_id_str] = cart.get(product_id_str, 0) + 1
    request.session['cart'] = cart

    return JsonResponse({
        'success': True,
        'message': f'Added {product.name} to cart!',
        'cart_count': sum(cart.values())
    })


@require_POST
@login_required_ajax
def add_to_wishlist(request):
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
    except (json.JSONDecodeError, AttributeError, ValueError):
        product_id = request.POST.get('product_id')

    product = get_object_or_404(Product, id=product_id)
    favorite, created = Favorite.objects.get_or_create(user=request.user, product=product)
    
    if created:
        message = f'Added {product.name} to wishlist!'
        is_favorited = True
    else:
        favorite.delete()
        message = f'Removed {product.name} from wishlist!'
        is_favorited = False

    wishlist_count = Favorite.objects.filter(user=request.user).count()
    return JsonResponse({
        'success': True,
        'message': message,
        'wishlist_count': wishlist_count,
        'is_favorited': is_favorited
    })


@require_POST
@login_required_ajax
def buy_now(request):
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
    except (json.JSONDecodeError, AttributeError, ValueError):
        product_id = request.POST.get('product_id')

    product = get_object_or_404(Product, id=product_id)
    return JsonResponse({
        'success': True,
        'message': f'Proceeding to checkout with {product.name}!',
        'checkout_url': '/checkout/'
    })


def wishlist_page(request):
    if not request.user.is_authenticated:
        return render(request, 'shop/wishlist.html', {'is_auth': False})
    
    favorites = Favorite.objects.filter(user=request.user).select_related('product').prefetch_related('product__images')
    return render(request, 'shop/wishlist.html', {
        'favorites': favorites,
        'is_auth': True
    })


@require_POST
@login_required_ajax
def remove_from_wishlist(request):
    try:
        data = json.loads(request.body)
        product_id = data.get('product_id')
    except (json.JSONDecodeError, AttributeError, ValueError):
        product_id = request.POST.get('product_id')

    favorite = get_object_or_404(Favorite, user=request.user, product_id=product_id)
    favorite.delete()
    
    wishlist_count = Favorite.objects.filter(user=request.user).count()
    return JsonResponse({
        'success': True,
        'message': 'Removed item from wishlist.',
        'wishlist_count': wishlist_count
    })


def cart_page(request):
    if not request.user.is_authenticated:
        return render(request, 'shop/cart.html', {'is_auth': False})
    
    cart = request.session.get('cart', {})
    valid_cart = {}
    if isinstance(cart, dict):
        for prod_id, qty in list(cart.items()):
            try:
                product_id = int(prod_id)
                quantity = int(qty)
            except (TypeError, ValueError):
                continue
            if quantity > 0 and Product.objects.filter(id=product_id).exists():
                valid_cart[str(product_id)] = quantity

    request.session['cart'] = valid_cart
    calculation = calculate_cart_totals(valid_cart, include_shipping=False)
    totals = calculation['totals']
    shipping, _ = calculate_shipping(totals['subtotal']) if totals['subtotal'] > 0 else (money(0), True)
    calculation = calculate_cart_totals(valid_cart, shipping=shipping, include_shipping=True)
    totals = calculation['totals']
    
    return render(request, 'shop/cart.html', {
        'cart_items': calculation['items'],
        'subtotal': totals['subtotal'],
        'original_total': totals['original_total'],
        'discount': totals['discount'],
        'taxable_amount': totals['taxable_amount'],
        'gst_amount': totals['gst_amount'],
        'cgst_amount': totals['cgst_amount'],
        'sgst_amount': totals['sgst_amount'],
        'igst_amount': totals['igst_amount'],
        'tax_type': totals['tax_type'],
        'gst_enabled': totals['gst_enabled'],
        'shipping': totals['shipping'],
        'grand_total': totals['grand_total'],
        'is_auth': True
    })


@require_POST
@login_required_ajax
def update_cart_quantity(request):
    try:
        data = json.loads(request.body)
        product_id = str(data.get('product_id'))
        quantity = int(data.get('quantity'))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'success': False, 'message': 'Invalid input data.'}, status=400)
        
    if quantity <= 0:
        return JsonResponse({'success': False, 'message': 'Quantity must be at least 1.'}, status=400)
        
    product = get_object_or_404(Product, id=product_id)
    
    # Check inventory
    if product.stock < quantity:
        return JsonResponse({
            'success': False, 
            'message': f'Only {product.stock} items available in stock.'
        }, status=400)
        
    cart = request.session.get('cart', {})
    cart[product_id] = quantity
    request.session['cart'] = cart

    base_calc = calculate_cart_totals(cart, include_shipping=False)
    shipping, _ = calculate_shipping(base_calc['totals']['subtotal']) if base_calc['totals']['subtotal'] > 0 else (money(0), True)
    calculation = calculate_cart_totals(cart, shipping=shipping, include_shipping=True)
    totals = calculation['totals']
    item_subtotal = next((item['line_total'] for item in calculation['items'] if str(item['product'].id) == product_id), money(0))
    cart_count = sum(cart.values())
    
    return JsonResponse({
        'success': True,
        'message': 'Quantity updated.',
        'item_subtotal': float(item_subtotal),
        'cart_count': cart_count,
        'subtotal': float(totals['subtotal']),
        'discount': float(totals['discount']),
        'taxable_amount': float(totals['taxable_amount']),
        'gst_amount': float(totals['gst_amount']),
        'cgst_amount': float(totals['cgst_amount']),
        'sgst_amount': float(totals['sgst_amount']),
        'igst_amount': float(totals['igst_amount']),
        'shipping': float(totals['shipping']),
        'grand_total': float(totals['grand_total'])
    })


@require_POST
@login_required_ajax
def remove_from_cart(request):
    try:
        data = json.loads(request.body)
        product_id = str(data.get('product_id'))
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'success': False, 'message': 'Invalid input data.'}, status=400)
        
    cart = request.session.get('cart', {})
    if product_id in cart:
        del cart[product_id]
        request.session['cart'] = cart

    base_calc = calculate_cart_totals(cart, include_shipping=False)
    shipping, _ = calculate_shipping(base_calc['totals']['subtotal']) if base_calc['totals']['subtotal'] > 0 else (money(0), True)
    calculation = calculate_cart_totals(cart, shipping=shipping, include_shipping=True)
    totals = calculation['totals']
    cart_count = sum(cart.values())
    
    return JsonResponse({
        'success': True,
        'message': 'Item removed from cart.',
        'cart_count': cart_count,
        'subtotal': float(totals['subtotal']),
        'discount': float(totals['discount']),
        'taxable_amount': float(totals['taxable_amount']),
        'gst_amount': float(totals['gst_amount']),
        'cgst_amount': float(totals['cgst_amount']),
        'sgst_amount': float(totals['sgst_amount']),
        'igst_amount': float(totals['igst_amount']),
        'shipping': float(totals['shipping']),
        'grand_total': float(totals['grand_total'])
    })


# ─── Checkout & Shipping Views ────────────────────────────────

import math

def _haversine_distance(lat1, lon1, lat2, lon2):
    """Calculates distance in kilometers between two coordinates."""
    try:
        degrees_to_radians = math.pi / 180.0
        phi1 = float(lat1) * degrees_to_radians
        phi2 = float(lat2) * degrees_to_radians
        theta1 = float(lon1) * degrees_to_radians
        theta2 = float(lon2) * degrees_to_radians
        
        cos = (math.sin(phi1) * math.sin(phi2) +
               math.cos(phi1) * math.cos(phi2) * math.cos(theta1 - theta2))
        cos = max(-1.0, min(1.0, cos))
        arc = math.acos(cos)
        return arc * 6371.0
    except Exception:
        return 0.0


def _cod_enabled():
    try:
        settings_obj = StoreAddressSettings.current()
    except Exception:
        return False
    return bool(getattr(settings_obj, 'cod_enabled', False))


def _cod_extra_charge():
    try:
        settings_obj = StoreAddressSettings.current()
    except Exception:
        return money(0)
    return money(getattr(settings_obj, 'cod_extra_charge', 0))


def checkout_page(request):
    if not request.user.is_authenticated:
        return redirect('/?login_required=1')

    pending_order_id = request.session.pop('pending_phonepe_order_id', None)
    if pending_order_id:
        pending_order = Order.objects.filter(
            id=pending_order_id,
            user=request.user,
            payment_method='PhonePe',
        ).first()
        if pending_order and pending_order.payment_status != Order.PaymentStatus.PAID:
            _discard_unpaid_phonepe_order(pending_order)
        
    cart = request.session.get('cart', {})
    if not cart:
        return redirect('cart_page')

    # Coupon handling
    from .models import Coupon
    applied_coupon = None
    coupon_discount = money(0)
    base_calculation = calculate_cart_totals(cart, include_shipping=False)
    subtotal = base_calculation['totals']['subtotal']
    applied_coupon_id = request.session.get('applied_coupon_id')
    if applied_coupon_id:
        c_obj = Coupon.objects.filter(pk=applied_coupon_id, is_active=True).first()
        if c_obj:
            valid, _ = c_obj.is_valid(subtotal)
            if valid:
                applied_coupon = c_obj
                coupon_discount = money(c_obj.calculate_discount(subtotal))
            else:
                request.session.pop('applied_coupon_id', None)

    # Ensure default coupons exist if db is empty for demonstration
    now = timezone.now()
    if not Coupon.objects.exists():
        Coupon.objects.create(
            code='WELCOME10',
            discount_type=Coupon.DiscountType.PERCENTAGE,
            discount_value=10.00,
            min_purchase=0.00,
            valid_from=now,
            valid_until=now + timedelta(days=30),
            is_active=True
        )
        Coupon.objects.create(
            code='SAVE200',
            discount_type=Coupon.DiscountType.FIXED,
            discount_value=200.00,
            min_purchase=999.00,
            valid_from=now,
            valid_until=now + timedelta(days=15),
            is_active=True
        )

    available_coupons = Coupon.objects.filter(
        is_active=True,
        valid_from__lte=now,
        valid_until__gte=now
    ).order_by('-created_at')

    default_shipping, _ = calculate_shipping(subtotal) if subtotal > 0 else (money(0), True)
    calculation = calculate_cart_totals(
        cart,
        coupon_discount=coupon_discount,
        shipping=default_shipping,
        include_shipping=True,
    )
    totals = calculation['totals']
    
    profile = None
    last_order = None
    cod_enabled = _cod_enabled()
    cod_extra_charge = _cod_extra_charge() if cod_enabled else money(0)
    display_grand_total = money(totals['grand_total'] + cod_extra_charge) if cod_enabled else totals['grand_total']
    if request.user.is_authenticated:
        profile = UserProfile.objects.filter(user=request.user).first()
        last_order = _customer_visible_orders(request.user).order_by('-created_at').first()
        
    return render(request, 'shop/checkout.html', {
        'cart_items': calculation['items'],
        'subtotal': totals['subtotal'],
        'discount': totals['discount'],
        'coupon_discount': totals['coupon_discount'],
        'taxable_amount': totals['taxable_amount'],
        'gst_amount': totals['gst_amount'],
        'cgst_amount': totals['cgst_amount'],
        'sgst_amount': totals['sgst_amount'],
        'igst_amount': totals['igst_amount'],
        'tax_type': totals['tax_type'],
        'gst_enabled': totals['gst_enabled'],
        'applied_coupon': applied_coupon,
        'available_coupons': available_coupons,
        'default_shipping': totals['shipping'],
        'base_grand_total': totals['grand_total'],
        'grand_total': display_grand_total,
        'config': calculation['config'],
        'profile': profile,
        'last_order': last_order,
        'cod_enabled': cod_enabled,
        'cod_extra_charge': cod_extra_charge,
    })


@require_POST
def apply_coupon(request):
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Authentication required to apply coupon.'}, status=401)

    try:
        data = json.loads(request.body)
        code = data.get('code', '').strip().upper()
    except (json.JSONDecodeError, AttributeError):
        code = request.POST.get('code', '').strip().upper()

    if not code:
        return JsonResponse({'success': False, 'message': 'Please enter a coupon code.'}, status=400)

    cart = request.session.get('cart', {})
    subtotal = calculate_cart_totals(cart, include_shipping=False)['totals']['subtotal']

    if subtotal <= 0:
        return JsonResponse({'success': False, 'message': 'Your cart is empty.'}, status=400)

    from .models import Coupon
    coupon = Coupon.objects.filter(code__iexact=code).first()
    if not coupon:
        return JsonResponse({'success': False, 'message': f"Coupon code '{code}' is invalid or does not exist."}, status=400)

    is_valid, msg = coupon.is_valid(subtotal)
    if not is_valid:
        return JsonResponse({'success': False, 'message': msg}, status=400)

    discount_amount = money(coupon.calculate_discount(subtotal))
    request.session['applied_coupon_id'] = coupon.id

    return JsonResponse({
        'success': True,
        'message': f"Coupon '{coupon.code}' applied! Saved Rs. {discount_amount:.2f}",
        'coupon_code': coupon.code,
        'coupon_id': coupon.id,
        'discount_amount': float(discount_amount),
        'discount_type': coupon.discount_type,
        'discount_value': float(coupon.discount_value),
    })


@require_POST
def remove_coupon(request):
    if 'applied_coupon_id' in request.session:
        del request.session['applied_coupon_id']
    return JsonResponse({'success': True, 'message': 'Coupon code removed successfully.'})


@require_POST
def verify_checkout_address(request):
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Authentication required.'}, status=401)
        
    try:
        data = json.loads(request.body)
        country = data.get('country', 'India').strip()
        state = data.get('state', '').strip()
        city = data.get('city', '').strip()
        county = data.get('county', '').strip()
    except (json.JSONDecodeError, ValueError, TypeError):
        return JsonResponse({'success': False, 'message': 'Invalid address inputs.'}, status=400)
        
    if not state:
        return JsonResponse({'success': False, 'message': 'State is required to check delivery availability.'}, status=400)
        
    # Check if delivery is allowed in this zone and find matching zone for rate
    matching_zone = None
    allowed_zones = DeliveryZone.objects.filter(is_active=True)
    
    if allowed_zones.exists():
        matching_zones = allowed_zones.filter(country__iexact=country, state__iexact=state)
        
        # Resolve most specific zone
        for zone in matching_zones:
            if zone.county and zone.county.lower() != county.lower():
                continue
            if zone.city and zone.city.lower() != city.lower():
                continue
            matching_zone = zone
            break
            
        if not matching_zone:
            return JsonResponse({
                'available': False,
                'message': f'Sorry, we do not deliver to {state} ({country}) at this time.'
            })
            
    base_calculation = calculate_cart_totals(request.session.get('cart', {}), include_shipping=False)
    subtotal = base_calculation['totals']['subtotal']
    delivery_fee, is_free = calculate_shipping(subtotal, matching_zone)

    coupon_discount = money(0)
    applied_coupon_id = request.session.get('applied_coupon_id')
    if applied_coupon_id:
        from .models import Coupon
        c_obj = Coupon.objects.filter(pk=applied_coupon_id, is_active=True).first()
        if c_obj:
            valid, _ = c_obj.is_valid(subtotal)
            if valid:
                coupon_discount = money(c_obj.calculate_discount(subtotal))

    calculation = calculate_cart_totals(
        request.session.get('cart', {}),
        customer_state=state,
        coupon_discount=coupon_discount,
        shipping=delivery_fee,
        include_shipping=True,
    )
    totals = calculation['totals']
            
    return JsonResponse({
        'available': True,
        'delivery_fee': float(delivery_fee),
        'cod_extra_charge': float(_cod_extra_charge() if _cod_enabled() else money(0)),
        'distance_km': 0.0,
        'is_free': is_free,
        'taxable_amount': float(totals['taxable_amount']),
        'gst_amount': float(totals['gst_amount']),
        'cgst_amount': float(totals['cgst_amount']),
        'sgst_amount': float(totals['sgst_amount']),
        'igst_amount': float(totals['igst_amount']),
        'grand_total': float(totals['grand_total']),
        'tax_type': totals['tax_type'],
        'message': 'Delivery is available for your address!'
    })


def _phonepe_enabled():
    return all([
        settings.PHONEPE_CLIENT_ID,
        settings.PHONEPE_CLIENT_SECRET,
        settings.PHONEPE_CLIENT_VERSION,
    ])


def _money_to_paise(value):
    amount = Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return int((amount * 100).to_integral_value(rounding=ROUND_HALF_UP))


def _phonepe_access_token():
    response = requests.post(
        settings.PHONEPE_AUTH_URL,
        data={
            'client_id': settings.PHONEPE_CLIENT_ID,
            'client_version': settings.PHONEPE_CLIENT_VERSION,
            'client_secret': settings.PHONEPE_CLIENT_SECRET,
            'grant_type': 'client_credentials',
        },
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        timeout=settings.PHONEPE_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get('access_token')
    if not token:
        raise ValueError('PhonePe auth response did not include access_token.')
    return token


def _phonepe_headers():
    return {
        'Content-Type': 'application/json',
        'Authorization': f'O-Bearer {_phonepe_access_token()}',
    }


def _phonepe_create_checkout(transaction_obj, request):
    redirect_url = request.build_absolute_uri(
        reverse('phonepe_payment_return', kwargs={'merchant_order_id': transaction_obj.merchant_order_id})
    )
    payload = {
        'merchantOrderId': transaction_obj.merchant_order_id,
        'amount': transaction_obj.amount_paise,
        'expireAfter': 1200,
        'metaInfo': {
            'udf1': f'order:{transaction_obj.order_id}',
            'udf2': transaction_obj.order.user.email[:256],
        },
        'paymentFlow': {
            'type': 'PG_CHECKOUT',
            'message': f'Payment for Order #{transaction_obj.order_id}',
            'merchantUrls': {
                'redirectUrl': redirect_url,
            },
        },
    }
    response = requests.post(
        f"{settings.PHONEPE_BASE_URL}/checkout/v2/pay",
        json=payload,
        headers=_phonepe_headers(),
        timeout=settings.PHONEPE_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    result = response.json()
    checkout_url = result.get('redirectUrl') or result.get('data', {}).get('redirectUrl')
    if not checkout_url:
        raise ValueError('PhonePe create order response did not include redirectUrl.')

    transaction_obj.request_payload = payload
    transaction_obj.response_payload = result
    transaction_obj.redirect_url = checkout_url
    transaction_obj.provider_order_id = result.get('orderId') or result.get('data', {}).get('orderId', '')
    transaction_obj.provider_state = result.get('state') or result.get('data', {}).get('state', '')
    transaction_obj.status = PaymentTransaction.Status.PENDING
    transaction_obj.save(update_fields=[
        'request_payload',
        'response_payload',
        'redirect_url',
        'provider_order_id',
        'provider_state',
        'status',
        'updated_at',
    ])
    return checkout_url


def _phonepe_order_status(merchant_order_id):
    response = requests.get(
        f"{settings.PHONEPE_BASE_URL}/checkout/v2/order/{merchant_order_id}/status",
        headers=_phonepe_headers(),
        timeout=settings.PHONEPE_REQUEST_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _extract_phonepe_payment_details(payload):
    data = payload.get('data') if isinstance(payload.get('data'), dict) else payload
    payment_details = data.get('paymentDetails') or []
    first_payment = payment_details[0] if payment_details else {}
    return {
        'merchant_order_id': data.get('merchantOrderId') or data.get('merchantTransactionId') or '',
        'provider_order_id': data.get('orderId') or '',
        'state': data.get('state') or first_payment.get('state') or '',
        'response_code': data.get('responseCode') or first_payment.get('responseCode') or '',
        'amount': data.get('amount') or first_payment.get('amount'),
        'transaction_id': first_payment.get('transactionId') or data.get('transactionId') or '',
    }


def _phonepe_status_to_local(state, response_code=''):
    normalized_state = (state or '').upper()
    normalized_code = (response_code or '').upper()
    if normalized_state == 'COMPLETED' or normalized_code in {'SUCCESS', 'PAYMENT_SUCCESS'}:
        return PaymentTransaction.Status.SUCCESS, Order.PaymentStatus.PAID, Order.Status.PROCESSING
    if normalized_state in {'FAILED'} or normalized_code in {'PAYMENT_ERROR', 'PAYMENT_DECLINED', 'AUTHORIZATION_FAILED'}:
        return PaymentTransaction.Status.FAILED, Order.PaymentStatus.FAILED, Order.Status.CANCELLED
    if normalized_state in {'CANCELLED', 'CANCELED'} or normalized_code in {'PAYMENT_CANCELLED', 'PAYMENT_CANCELED'}:
        return PaymentTransaction.Status.CANCELLED, Order.PaymentStatus.CANCELLED, Order.Status.CANCELLED
    if normalized_state == 'TIMED_OUT' or normalized_code in {'PAYMENT_TIMEOUT', 'TIMED_OUT'}:
        return PaymentTransaction.Status.TIMEOUT, Order.PaymentStatus.TIMEOUT, Order.Status.CANCELLED
    return PaymentTransaction.Status.PENDING, Order.PaymentStatus.PENDING, Order.Status.PENDING


def _reconcile_phonepe_order(merchant_order_id):
    with transaction.atomic():
        payment_txn = (
            PaymentTransaction.objects.select_for_update()
            .select_related('order')
            .get(merchant_order_id=merchant_order_id)
        )

    status_payload = _phonepe_order_status(merchant_order_id)
    details = _extract_phonepe_payment_details(status_payload)
    state = details['state']
    response_code = details['response_code']
    provider_amount = details['amount']
    local_status, order_payment_status, order_status = _phonepe_status_to_local(state, response_code)

    if provider_amount is not None and int(provider_amount) != payment_txn.amount_paise:
        local_status = PaymentTransaction.Status.FAILED
        order_payment_status = Order.PaymentStatus.FAILED
        response_code = response_code or 'AMOUNT_MISMATCH'

    if details['merchant_order_id'] and details['merchant_order_id'] != payment_txn.merchant_order_id:
        local_status = PaymentTransaction.Status.FAILED
        order_payment_status = Order.PaymentStatus.FAILED
        response_code = response_code or 'ORDER_ID_MISMATCH'

    with transaction.atomic():
        payment_txn = (
            PaymentTransaction.objects.select_for_update()
            .select_related('order')
            .get(pk=payment_txn.pk)
        )
        order = payment_txn.order
        was_paid = order.payment_status == Order.PaymentStatus.PAID
        was_cod_order = order.payment_method.upper() == 'COD'

        payment_txn.status = local_status
        payment_txn.provider_state = state
        payment_txn.response_code = response_code
        payment_txn.provider_order_id = details['provider_order_id'] or payment_txn.provider_order_id
        payment_txn.provider_transaction_id = details['transaction_id'] or payment_txn.provider_transaction_id
        payment_txn.response_payload = status_payload
        payment_txn.verified_at = timezone.now()
        payment_txn.save()

        order.phonepe_payment_state = state
        order.phonepe_response_code = response_code
        order.phonepe_order_id = payment_txn.provider_order_id
        order.phonepe_transaction_id = payment_txn.provider_transaction_id
        if local_status == PaymentTransaction.Status.SUCCESS:
            order.payment_status = order_payment_status
            order.payment_method = 'PhonePe'
            if not was_paid and not was_cod_order:
                _finalize_paid_order(order)
            if not was_cod_order or order.status in [Order.Status.PENDING, Order.Status.PROCESSING]:
                order.status = order_status
            order.payment_id = payment_txn.provider_transaction_id or payment_txn.provider_order_id
        elif local_status in [
            PaymentTransaction.Status.FAILED,
            PaymentTransaction.Status.CANCELLED,
            PaymentTransaction.Status.TIMEOUT,
        ]:
            if was_cod_order:
                order.payment_status = Order.PaymentStatus.PENDING
            else:
                order.payment_status = order_payment_status
                order.status = order_status
            if not was_cod_order and not order.cancelled_at:
                order.cancelled_at = timezone.now()
        else:
            order.payment_status = order_payment_status
        order.save(update_fields=[
            'payment_method',
            'payment_status',
            'phonepe_payment_state',
            'phonepe_response_code',
            'phonepe_order_id',
            'phonepe_transaction_id',
            'status',
            'payment_id',
            'cancelled_at',
            'updated_at',
        ])

    return payment_txn


def _phonepe_webhook_authorized(request):
    expected_user = settings.PHONEPE_WEBHOOK_USERNAME
    expected_password = settings.PHONEPE_WEBHOOK_PASSWORD
    if not expected_user or not expected_password:
        return False
    header = request.META.get('HTTP_AUTHORIZATION', '')
    if not header.lower().startswith('basic '):
        return False
    try:
        decoded = base64.b64decode(header.split(' ', 1)[1]).decode('utf-8')
    except Exception:
        return False
    username, separator, password = decoded.partition(':')
    return bool(separator) and secrets.compare_digest(username, expected_user) and secrets.compare_digest(password, expected_password)


def _extract_webhook_merchant_order_id(payload):
    candidates = [payload]
    if isinstance(payload.get('payload'), dict):
        candidates.append(payload['payload'])
    if isinstance(payload.get('data'), dict):
        candidates.append(payload['data'])
    for item in candidates:
        merchant_order_id = item.get('merchantOrderId') or item.get('merchantTransactionId') or item.get('orderId')
        if merchant_order_id:
            return merchant_order_id
    return ''


def _finalize_paid_order(order):
    if order.coupon_id:
        order.coupon.used_count += 1
        order.coupon.save(update_fields=['used_count'])

    for item in order.items.select_related('product'):
        prod = item.product
        if not prod:
            continue
        prod.stock -= item.quantity
        if prod.stock <= 0:
            prod.status = Product.Status.OUT_OF_STOCK
        prod.save()

    send_order_status_email(order, 'placed')


def _discard_unpaid_phonepe_order(order):
    if order.payment_method.lower() != 'phonepe' or order.payment_status == Order.PaymentStatus.PAID:
        return False
    order.delete()
    return True


def _order_can_pay_online_before_delivery(order):
    return (
        order.payment_method.upper() == 'COD'
        and order.payment_status != Order.PaymentStatus.PAID
        and order.status in [Order.Status.PENDING, Order.Status.PROCESSING, Order.Status.SHIPPED]
        and not order.cancel_requested
    )


def _customer_visible_orders(user):
    return (
        Order.objects.filter(user=user)
        .exclude(
            payment_method='PhonePe',
            payment_status__in=[
                Order.PaymentStatus.PENDING,
                Order.PaymentStatus.FAILED,
                Order.PaymentStatus.CANCELLED,
                Order.PaymentStatus.TIMEOUT,
            ],
        )
    )


def _create_order_from_checkout(request, cart, data, totals, calculation, applied_coupon):
    payment_method = data.get('payment_method', 'PhonePe').strip() or 'PhonePe'
    is_phonepe = payment_method.lower() in {'phonepe', 'online', 'card', 'upi'}
    is_cod = payment_method.upper() == 'COD'
    cod_extra_charge = money(totals.get('cod_extra_charge', 0)) if is_cod else money(0)
    merchant_order_id = f"ORD-{uuid.uuid4().hex[:24].upper()}" if is_phonepe else ''
    payment_status = Order.PaymentStatus.PENDING
    order_status = Order.Status.PENDING

    order = Order.objects.create(
        user=request.user,
        full_name=data['full_name'],
        phone=data['phone'],
        country=data['country'],
        state=data['state'],
        city=data['city'],
        county=data['county'],
        street_address=data['street_address'],
        postal_code=data['postal_code'],
        subtotal=totals['subtotal'],
        delivery_fee=totals['shipping'],
        cod_extra_charge=cod_extra_charge,
        discount=totals['coupon_discount'],
        taxable_amount=totals['taxable_amount'],
        gst_amount=totals['gst_amount'],
        cgst_amount=totals['cgst_amount'],
        sgst_amount=totals['sgst_amount'],
        igst_amount=totals['igst_amount'],
        tax_type=totals['tax_type'],
        gst_enabled_snapshot=totals['gst_enabled'],
        seller_state_snapshot=totals['seller_state'],
        coupon=applied_coupon,
        coupon_code=applied_coupon.code if applied_coupon else '',
        total=money(totals['grand_total'] + cod_extra_charge),
        status=order_status,
        payment_method='PhonePe' if is_phonepe else payment_method,
        payment_status=payment_status,
        phonepe_merchant_order_id=merchant_order_id,
    )

    if applied_coupon and not is_phonepe:
        applied_coupon.used_count += 1
        applied_coupon.save(update_fields=['used_count'])
        request.session.pop('applied_coupon_id', None)

    for item in calculation['items']:
        prod = item['product']
        qty = item['quantity']
        OrderItem.objects.create(
            order=order,
            product=prod,
            product_name=prod.name,
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
        if not is_phonepe:
            prod.stock -= qty
            if prod.stock <= 0:
                prod.status = Product.Status.OUT_OF_STOCK
            prod.save()

    return order, is_phonepe


@require_POST
def place_order(request):
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Authentication required.'}, status=401)
        
    cart = request.session.get('cart', {})
    if not cart:
        return JsonResponse({'success': False, 'message': 'Your cart is empty.'}, status=400)
        
    try:
        data = json.loads(request.body)
        full_name = data.get('full_name', '').strip()
        phone = data.get('phone', '').strip()
        country = data.get('country', '').strip()
        state = data.get('state', '').strip()
        city = data.get('city', '').strip()
        county = data.get('county', '').strip()
        street_address = data.get('street_address', '').strip()
        postal_code = data.get('postal_code', '').strip()
        payment_method = data.get('payment_method', 'PhonePe').strip() or 'PhonePe'
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid checkout data.'}, status=400)
        
    if not (full_name and phone and country and state and city and street_address and postal_code):
        return JsonResponse({'success': False, 'message': 'Please fill all required address fields.'}, status=400)
    if payment_method.upper() == 'COD' and not _cod_enabled():
        return JsonResponse({'success': False, 'message': 'Cash on Delivery is currently unavailable.'}, status=400)
    if payment_method.lower() in {'phonepe', 'online', 'card', 'upi'} and not _phonepe_enabled():
        return JsonResponse({'success': False, 'message': 'PhonePe sandbox credentials are not configured.'}, status=503)
        
    # Re-verify delivery availability and calculate fee securely
    matching_zone = None
    allowed_zones = DeliveryZone.objects.filter(is_active=True)
    
    if allowed_zones.exists():
        matching_zones = allowed_zones.filter(country__iexact=country, state__iexact=state)
        for zone in matching_zones:
            if zone.county and zone.county.lower() != county.lower():
                continue
            if zone.city and zone.city.lower() != city.lower():
                continue
            matching_zone = zone
            break
            
        if not matching_zone:
            return JsonResponse({'success': False, 'message': 'Selected shipping address is outside our delivery zones.'}, status=400)
            
    # Validate stock before calculating final totals.
    for prod_id, qty in cart.items():
        prod = Product.objects.filter(id=int(prod_id)).first()
        if not prod:
            continue
            
        if prod.stock < qty:
            return JsonResponse({
                'success': False,
                'message': f'Sorry, {prod.name} only has {prod.stock} items left in stock.'
            }, status=400)

    base_calculation = calculate_cart_totals(cart, include_shipping=False)
    subtotal = base_calculation['totals']['subtotal']
    delivery_fee, _ = calculate_shipping(subtotal, matching_zone)

    # Coupon discount processing
    from .models import Coupon
    coupon_discount = money(0)
    applied_coupon = None
    applied_coupon_id = request.session.get('applied_coupon_id')
    if applied_coupon_id:
        c_obj = Coupon.objects.filter(pk=applied_coupon_id, is_active=True).first()
        if c_obj:
            valid, _ = c_obj.is_valid(subtotal)
            if valid:
                applied_coupon = c_obj
                coupon_discount = money(c_obj.calculate_discount(subtotal))

    calculation = calculate_order_totals(
        cart,
        customer_state=state,
        coupon_discount=coupon_discount,
        shipping=delivery_fee,
    )
    totals = calculation['totals']
    if payment_method.upper() == 'COD':
        totals['cod_extra_charge'] = _cod_extra_charge()

    checkout_data = {
        'full_name': full_name,
        'phone': phone,
        'country': country,
        'state': state,
        'city': city,
        'county': county,
        'street_address': street_address,
        'postal_code': postal_code,
        'payment_method': payment_method,
    }

    with transaction.atomic():
        UserProfile.objects.update_or_create(
            user=request.user,
            defaults={
                'phone': phone,
                'street_address': street_address,
                'city': city,
                'county': county,
                'state': state,
                'country': country,
                'postal_code': postal_code,
            },
        )
        order, is_phonepe = _create_order_from_checkout(
            request,
            cart,
            checkout_data,
            totals,
            calculation,
            applied_coupon,
        )
        payment_txn = None
        if is_phonepe:
            payment_txn = PaymentTransaction.objects.create(
                order=order,
                merchant_order_id=order.phonepe_merchant_order_id,
                amount_paise=_money_to_paise(order.total),
            )

    if is_phonepe:
        try:
            redirect_url = _phonepe_create_checkout(payment_txn, request)
        except Exception as exc:
            payment_txn.status = PaymentTransaction.Status.FAILED
            payment_txn.response_code = 'CREATE_ORDER_FAILED'
            payment_txn.response_payload = {'error': str(exc)}
            payment_txn.save(update_fields=['status', 'response_code', 'response_payload', 'updated_at'])
            order.payment_status = Order.PaymentStatus.FAILED
            order.phonepe_response_code = 'CREATE_ORDER_FAILED'
            order.save(update_fields=['payment_status', 'phonepe_response_code', 'updated_at'])
            _discard_unpaid_phonepe_order(order)
            return JsonResponse({'success': False, 'message': 'Could not start PhonePe payment. Please try again.'}, status=502)

        request.session['pending_phonepe_order_id'] = order.id
        return JsonResponse({
            'success': True,
            'message': 'Redirecting to PhonePe checkout.',
            'order_id': order.id,
            'redirect_url': redirect_url,
            'merchant_order_id': payment_txn.merchant_order_id,
        })

    request.session['cart'] = {}

    # Send Order Placed Email to customer
    send_order_status_email(order, 'placed')

    return JsonResponse({
        'success': True,
        'message': 'Order placed successfully!',
        'order_id': order.id
    })


def order_success(request, order_id):
    if not request.user.is_authenticated:
        return redirect('/')
        
    order = get_object_or_404(Order.objects.prefetch_related('items'), id=order_id, user=request.user)
    return render(request, 'shop/order_success.html', {'order': order})


def _customer_invoice_available(order):
    if order.payment_status == Order.PaymentStatus.PAID:
        return True
    return order.payment_method.upper() == 'COD' and order.status == Order.Status.DELIVERED


def build_invoice_share_token(order):
    return signing.TimestampSigner(salt='shop.invoice.share').sign(str(order.id))


def _invoice_share_max_age_seconds():
    days = int(getattr(settings, 'WHATSAPP_INVOICE_LINK_MAX_AGE_DAYS', 30))
    return max(1, days) * 24 * 60 * 60


def _pdf_escape(value):
    return str(value or '').replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')


def _simple_pdf_bytes(title, lines):
    width, height = 595, 842
    y = height - 72
    content = ['BT', '/F1 18 Tf', f'72 {y} Td', f'({_pdf_escape(title)}) Tj', 'ET']
    y -= 34
    for line in lines:
        if y < 60:
            break
        if line == '':
            y -= 12
            continue
        content.extend(['BT', '/F1 10 Tf', f'72 {y} Td', f'({_pdf_escape(line)}) Tj', 'ET'])
        y -= 15
    stream = '\n'.join(content).encode('latin-1', errors='replace')
    objects = [
        b'1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n',
        b'2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n',
        b'3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n',
        b'4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n',
        f'5 0 obj << /Length {len(stream)} >> stream\n'.encode('ascii') + stream + b'\nendstream endobj\n',
    ]
    pdf = bytearray(b'%PDF-1.4\n')
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
    xref_position = len(pdf)
    pdf.extend(f'xref\n0 {len(objects) + 1}\n'.encode('ascii'))
    pdf.extend(b'0000000000 65535 f \n')
    for offset in offsets:
        pdf.extend(f'{offset:010d} 00000 n \n'.encode('ascii'))
    pdf.extend(f'trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_position}\n%%EOF\n'.encode('ascii'))
    return bytes(pdf)


def _invoice_pdf_response(order):
    def invoice_datetime(value):
        if not value:
            return '-'
        return timezone.localtime(value).strftime('%d %b %Y, %I:%M %p IST')

    store_settings = StoreAddressSettings.current()
    gst_config = GSTConfig.current()
    payment_txn = PaymentTransaction.objects.filter(order=order).order_by('-created_at').first()
    company_name = (
        (getattr(gst_config, 'seller_name', '') or '').strip()
        or (getattr(store_settings, 'sender_name', '') or '').strip()
        or 'Kj Apperals'
    )
    company_phone = (getattr(store_settings, 'sender_phone', '') or '').strip()
    company_address = ' '.join(
        line.strip()
        for line in (getattr(gst_config, 'seller_address', '') or getattr(store_settings, 'sender_address', '') or '').splitlines()
        if line.strip()
    )
    invoice_uid = f'INV-{order.created_at:%Y%m%d}-{order.id:06d}'

    lines = [
        company_name,
        'Thank you for shopping with us.',
        'This invoice confirms your order and payment/delivery details.',
        '',
        f'Invoice Number: {invoice_uid}',
        f'Invoice Date: {invoice_datetime(order.created_at)}',
        f'Order ID: ORDER-{order.id:06d}',
        f'Order Date: {invoice_datetime(order.created_at)}',
        f'Order Status: {order.get_status_display()}',
        f'Delivery Date: {invoice_datetime(order.delivered_at) if order.delivered_at else "Not delivered yet"}',
        f'Delivery Location: {order.city}, {order.state} - {order.postal_code}',
        f'Payment Method: {order.payment_method}',
        f'Payment Status: {order.get_payment_status_display()}',
        f'Payment Date: {invoice_datetime(getattr(payment_txn, "verified_at", None) or getattr(payment_txn, "updated_at", None)) if order.payment_status == Order.PaymentStatus.PAID else "-"}',
        f'Payment Provider: {getattr(payment_txn, "provider", "") or ("COD" if order.payment_method.upper() == "COD" else "-")}',
        f'Merchant Order ID: {order.phonepe_merchant_order_id or getattr(payment_txn, "merchant_order_id", "") or "-"}',
        f'Provider Order ID: {order.phonepe_order_id or getattr(payment_txn, "provider_order_id", "") or "-"}',
        f'Transaction ID: {order.phonepe_transaction_id or getattr(payment_txn, "provider_transaction_id", "") or "-"}',
        f'Provider Status: {getattr(payment_txn, "provider_state", "") or getattr(payment_txn, "status", "") or "-"}',
        f'Customer Name: {order.full_name}',
        '',
        'Seller / Company Details',
        f'Company Name: {company_name}',
        f'Company Phone: {company_phone or "-"}',
        f'Company Address: {company_address or "-"}',
        f'GSTIN: {gst_config.seller_gstin or "-"}',
        '',
        'Delivery Location',
        f'{order.full_name} ({order.phone})',
        order.street_address,
        f'{order.city}, {order.state} - {order.postal_code}',
        order.country,
        '',
        'Order List',
    ]
    for index, item in enumerate(order.items.all(), start=1):
        lines.append(f'{index}. {item.product_name} | Qty {item.quantity} | Rate Rs. {item.price} | Amount Rs. {item.final_amount}')
        if order.gst_enabled_snapshot:
            lines.append(f'  HSN/SAC {item.hsn_sac_code or "-"} | GST {item.gst_percentage}% | Tax Rs. {item.gst_amount}')

    lines.extend([
        '',
        f'Subtotal: Rs. {order.subtotal}',
        f'Delivery Fee: Rs. {order.delivery_fee}',
    ])
    if order.cod_extra_charge > 0:
        lines.append(f'COD Extra Charge: Rs. {order.cod_extra_charge}')
    if order.discount > 0:
        lines.append(f'Discount: - Rs. {order.discount}')
    if order.gst_enabled_snapshot or order.gst_amount > 0:
        lines.extend([
            f'Taxable Amount: Rs. {order.taxable_amount}',
            f'CGST: Rs. {order.cgst_amount}',
            f'SGST: Rs. {order.sgst_amount}',
            f'IGST: Rs. {order.igst_amount}',
            f'Total GST: Rs. {order.gst_amount}',
            f'Seller State: {order.seller_state_snapshot or "-"}',
        ])
    lines.append(f'Grand Total: Rs. {order.total}')

    response = HttpResponse(
        _simple_pdf_bytes(f'{company_name} - Invoice {invoice_uid}', lines),
        content_type='application/pdf',
    )
    response['Content-Disposition'] = f'attachment; filename="invoice-{invoice_uid}.pdf"'
    return response


def download_customer_invoice(request, order_id):
    if not request.user.is_authenticated:
        return redirect('/?login_required=1')

    order = get_object_or_404(
        Order.objects.prefetch_related('items'),
        id=order_id,
        user=request.user,
    )
    if not _customer_invoice_available(order):
        return HttpResponse('Invoice is available after payment is completed or COD order is delivered.', status=403)
    return _invoice_pdf_response(order)


def download_shared_invoice(request, order_id, token):
    try:
        signed_order_id = signing.TimestampSigner(salt='shop.invoice.share').unsign(
            token,
            max_age=_invoice_share_max_age_seconds(),
        )
    except SignatureExpired:
        return HttpResponse('This invoice link has expired. Please contact support for a new invoice link.', status=403)
    except BadSignature:
        return HttpResponse('Invalid invoice link.', status=403)

    if str(signed_order_id) != str(order_id):
        return HttpResponse('Invalid invoice link.', status=403)

    order = get_object_or_404(Order.objects.prefetch_related('items'), id=order_id)
    if not _customer_invoice_available(order):
        return HttpResponse('Invoice is available after payment is completed or COD order is delivered.', status=403)
    return _invoice_pdf_response(order)


def phonepe_payment_return(request, merchant_order_id):
    if not request.user.is_authenticated:
        return redirect('/?login_required=1')

    payment_txn = get_object_or_404(
        PaymentTransaction.objects.select_related('order'),
        merchant_order_id=merchant_order_id,
        order__user=request.user,
    )
    try:
        payment_txn = _reconcile_phonepe_order(merchant_order_id)
    except Exception:
        payment_txn.order.phonepe_response_code = payment_txn.order.phonepe_response_code or 'VERIFY_FAILED'
        payment_txn.order.save(update_fields=['phonepe_response_code', 'updated_at'])
    else:
        if payment_txn.order.payment_status == Order.PaymentStatus.PAID:
            if request.session.get('pending_phonepe_order_id') == payment_txn.order_id:
                request.session['cart'] = {}
                request.session.pop('applied_coupon_id', None)
                request.session.pop('pending_phonepe_order_id', None)
        else:
            if payment_txn.order.payment_method.upper() == 'COD':
                return redirect('profile_page')
            _discard_unpaid_phonepe_order(payment_txn.order)
            request.session.pop('pending_phonepe_order_id', None)
            return redirect('/checkout/')

    return redirect('order_success', order_id=payment_txn.order_id)


def phonepe_payment_retry(request, order_id):
    if not request.user.is_authenticated:
        return redirect('/?login_required=1')
    if not _phonepe_enabled():
        return redirect('order_success', order_id=order_id)

    order = get_object_or_404(Order, id=order_id, user=request.user)
    if order.payment_status == Order.PaymentStatus.PAID:
        return redirect('order_success', order_id=order.id)

    existing = (
        order.payment_transactions
        .filter(status__in=[PaymentTransaction.Status.INITIATED, PaymentTransaction.Status.PENDING])
        .order_by('-created_at')
        .first()
    )
    if existing and existing.redirect_url:
        return redirect(existing.redirect_url)

    merchant_order_id = f"ORD-{uuid.uuid4().hex[:24].upper()}"
    order.phonepe_merchant_order_id = merchant_order_id
    order.payment_method = 'PhonePe'
    order.payment_status = Order.PaymentStatus.PENDING
    order.status = Order.Status.PENDING
    order.cancelled_at = None
    order.phonepe_payment_state = ''
    order.phonepe_response_code = ''
    order.save(update_fields=[
        'phonepe_merchant_order_id',
        'payment_method',
        'payment_status',
        'status',
        'cancelled_at',
        'phonepe_payment_state',
        'phonepe_response_code',
        'updated_at',
    ])
    payment_txn = PaymentTransaction.objects.create(
        order=order,
        merchant_order_id=merchant_order_id,
        amount_paise=_money_to_paise(order.total),
    )
    try:
        redirect_url = _phonepe_create_checkout(payment_txn, request)
    except Exception:
        payment_txn.status = PaymentTransaction.Status.FAILED
        payment_txn.response_code = 'CREATE_ORDER_FAILED'
        payment_txn.save(update_fields=['status', 'response_code', 'updated_at'])
        order.payment_status = Order.PaymentStatus.FAILED
        order.phonepe_response_code = 'CREATE_ORDER_FAILED'
        order.save(update_fields=['payment_status', 'phonepe_response_code', 'updated_at'])
        return redirect('order_success', order_id=order.id)

    return redirect(redirect_url)


def cod_pay_online(request, order_id):
    if not request.user.is_authenticated:
        return redirect('/?login_required=1')
    if not _phonepe_enabled():
        return redirect('profile_page')

    order = get_object_or_404(Order, id=order_id, user=request.user)
    if not _order_can_pay_online_before_delivery(order):
        return redirect('profile_page')

    existing = (
        order.payment_transactions
        .filter(status__in=[PaymentTransaction.Status.INITIATED, PaymentTransaction.Status.PENDING])
        .order_by('-created_at')
        .first()
    )
    if existing and existing.redirect_url:
        return redirect(existing.redirect_url)

    merchant_order_id = f"ORD-{uuid.uuid4().hex[:24].upper()}"
    order.phonepe_merchant_order_id = merchant_order_id
    order.phonepe_payment_state = ''
    order.phonepe_response_code = ''
    order.save(update_fields=[
        'phonepe_merchant_order_id',
        'phonepe_payment_state',
        'phonepe_response_code',
        'updated_at',
    ])
    payment_txn = PaymentTransaction.objects.create(
        order=order,
        merchant_order_id=merchant_order_id,
        amount_paise=_money_to_paise(order.total),
    )
    try:
        redirect_url = _phonepe_create_checkout(payment_txn, request)
    except Exception:
        payment_txn.status = PaymentTransaction.Status.FAILED
        payment_txn.response_code = 'CREATE_ORDER_FAILED'
        payment_txn.save(update_fields=['status', 'response_code', 'updated_at'])
        order.phonepe_response_code = 'CREATE_ORDER_FAILED'
        order.save(update_fields=['phonepe_response_code', 'updated_at'])
        return redirect('profile_page')

    return redirect(redirect_url)


@require_POST
def phonepe_payment_status(request, order_id):
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Authentication required.'}, status=401)

    order = get_object_or_404(Order, id=order_id, user=request.user)
    if not order.phonepe_merchant_order_id:
        return JsonResponse({'success': False, 'message': 'This order does not have a PhonePe payment.'}, status=400)

    try:
        payment_txn = _reconcile_phonepe_order(order.phonepe_merchant_order_id)
    except Exception:
        return JsonResponse({'success': False, 'message': 'Unable to verify PhonePe status right now.'}, status=502)

    return JsonResponse({
        'success': True,
        'order_id': order.id,
        'payment_status': payment_txn.order.payment_status,
        'phonepe_state': payment_txn.provider_state,
        'transaction_id': payment_txn.provider_transaction_id,
    })


@csrf_exempt
@require_POST
def phonepe_webhook(request):
    if not _phonepe_webhook_authorized(request):
        return JsonResponse({'success': False, 'message': 'Unauthorized webhook.'}, status=401)

    raw_body = request.body or b''
    payload_hash = hashlib.sha256(raw_body).hexdigest()
    try:
        payload = json.loads(raw_body.decode('utf-8') or '{}')
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'message': 'Invalid webhook JSON.'}, status=400)

    merchant_order_id = _extract_webhook_merchant_order_id(payload)
    event_id = str(payload.get('eventId') or payload.get('event') or payload.get('transactionId') or '')
    try:
        webhook_event = PaymentWebhookEvent.objects.create(
            event_id=event_id,
            payload_hash=payload_hash,
            merchant_order_id=merchant_order_id,
            payload=payload,
        )
    except IntegrityError:
        return JsonResponse({'success': True, 'message': 'Duplicate webhook ignored.'})

    if not merchant_order_id:
        webhook_event.processed = True
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['processed', 'processed_at'])
        return JsonResponse({'success': True, 'message': 'Webhook received without merchant order ID.'})

    payment_txn = PaymentTransaction.objects.filter(merchant_order_id=merchant_order_id).first()
    webhook_event.transaction = payment_txn
    webhook_event.save(update_fields=['transaction'])
    if not payment_txn:
        webhook_event.processed = True
        webhook_event.processed_at = timezone.now()
        webhook_event.save(update_fields=['processed', 'processed_at'])
        return JsonResponse({'success': True, 'message': 'Webhook received for unknown order.'})

    _reconcile_phonepe_order(merchant_order_id)
    webhook_event.processed = True
    webhook_event.processed_at = timezone.now()
    webhook_event.save(update_fields=['processed', 'processed_at'])
    return JsonResponse({'success': True})


def profile_page(request):
    if not request.user.is_authenticated:
        return redirect('/?login_required=1')
        
    profile, created = UserProfile.objects.get_or_create(user=request.user)
    orders = (
        _customer_visible_orders(request.user)
        .prefetch_related('items', 'items__product', 'items__product__images')
        .order_by('-created_at')
    )
    
    return render(request, 'shop/profile.html', {
        'profile': profile,
        'orders': orders,
        'is_auth': True
    })


@require_POST
def save_profile_address(request):
    if not request.user.is_authenticated:
        return JsonResponse({'success': False, 'message': 'Authentication required.'}, status=401)
        
    try:
        data = json.loads(request.body)
        phone = data.get('phone', '').strip()
        street_address = data.get('street_address', '').strip()
        city = data.get('city', '').strip()
        county = data.get('county', '').strip()
        state = data.get('state', '').strip()
        country = data.get('country', 'India').strip()
        postal_code = data.get('postal_code', '').strip()
        
        if not street_address or not city or not state or not postal_code:
            return JsonResponse({'success': False, 'message': 'Please fill out all required fields (*).'}, status=400)
            
        profile, created = UserProfile.objects.get_or_create(user=request.user)
        profile.phone = phone
        profile.street_address = street_address
        profile.city = city
        profile.county = county
        profile.state = state
        profile.country = country
        profile.postal_code = postal_code
        profile.save()
        
        return JsonResponse({'success': True, 'message': 'Address saved successfully!'})
    except Exception as e:
        return JsonResponse({'success': False, 'message': str(e)}, status=500)


@require_POST
@login_required_ajax
def cancel_order(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    # Main rule: customer cancel button allowed only ONE TIME
    if order.cancel_requested or order.status == Order.Status.CANCELLED:
        return JsonResponse({
            'success': False,
            'already_attempted': True,
            'message': 'Contact customer care! You have already submitted or attempted a cancellation for this order.'
        }, status=400)

    # Order must be before Shipped status (Pending or Processing)
    if order.status not in [Order.Status.PENDING, Order.Status.PROCESSING]:
        return JsonResponse({
            'success': False,
            'message': 'Contact customer care! Order cannot be cancelled as it has already been shipped or processed.'
        }, status=400)

    try:
        data = json.loads(request.body)
        reason = data.get('reason', '').strip()
    except (json.JSONDecodeError, AttributeError):
        reason = request.POST.get('reason', '').strip()

    if not reason:
        return JsonResponse({
            'success': False,
            'message': 'Please provide a reason for cancelling your order.'
        }, status=400)

    order.status = Order.Status.CANCELLED
    order.cancellation_reason = reason
    order.cancelled_at = timezone.now()
    order.cancel_requested = True
    order.save()

    # Restore stock for cancelled items
    for item in order.items.all():
        if item.product:
            item.product.stock += item.quantity
            if item.product.status == Product.Status.OUT_OF_STOCK and item.product.stock > 0:
                item.product.status = Product.Status.AVAILABLE
            item.product.save()

    # Send Order Cancelled Email to customer
    send_order_status_email(order, 'cancelled')

    # Refund message for Online / Card payment
    is_online_payment = order.payment_method.lower() in ['phonepe', 'card', 'online', 'upi', 'netbanking', 'credit_card', 'debit_card']
    if is_online_payment:
        refund_notice = f"Your order #{order.id} has been cancelled. Since you paid via {order.payment_method}, your refund of ₹{order.total} will be processed within 2 to 5 working days."
    else:
        refund_notice = f"Your order #{order.id} has been cancelled successfully."

    return JsonResponse({
        'success': True,
        'message': refund_notice,
        'status': order.status,
        'status_display': order.get_status_display()
    })


@require_POST
@login_required_ajax
def return_order(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)

    # Main rule: customer return/replacement button allowed only ONE TIME
    if order.return_requested or order.status in [Order.Status.RETURN_REQUESTED, Order.Status.RETURNED]:
        return JsonResponse({
            'success': False,
            'already_attempted': True,
            'message': 'Contact customer care! You have already submitted a replacement request for this order.'
        }, status=400)

    # Replacement only allowed after order delivery
    if order.status != Order.Status.DELIVERED:
        return JsonResponse({
            'success': False,
            'message': 'Contact customer care! Replacement is only available after order delivery.'
        }, status=400)

    # Replacement button allowed only within 7 days after delivery
    if not order.is_within_7_days:
        return JsonResponse({
            'success': False,
            'message': 'Contact customer care! The 7-day replacement window for this order has expired.'
        }, status=400)

    try:
        data = json.loads(request.body)
        reason = data.get('reason', '').strip()
    except (json.JSONDecodeError, AttributeError):
        reason = request.POST.get('reason', '').strip()

    if not reason:
        return JsonResponse({
            'success': False,
            'message': 'Please provide a reason for replacement.'
        }, status=400)

    order.status = Order.Status.RETURN_REQUESTED
    order.return_reason = reason
    order.returned_at = timezone.now()
    order.return_requested = True
    order.save()

    # Send Replacement Requested Email to customer
    send_order_status_email(order, 'replacement_requested')

    refund_notice = f"Replacement request submitted for Order #{order.id}. Your refund/replacement will be processed within 2 to 5 working days."

    return JsonResponse({
        'success': True,
        'message': refund_notice,
        'status': order.status,
        'status_display': order.get_status_display()
    })


