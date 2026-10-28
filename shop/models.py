from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

from .utils import unique_slug


# ─── Existing AdminUser / LoginAttempt stay here ──────────────
# (keep your AdminUser and LoginAttempt models above this)


# ─── Category ─────────────────────────────────────────────────
class Category(models.Model):
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active'
        INACTIVE = 'inactive', 'Inactive'

    name        = models.CharField(max_length=100, unique=True)
    slug        = models.SlugField(max_length=100, unique=True, blank=True)
    image       = models.ImageField(upload_to='categories/%Y/%m/', blank=True, null=True)
    description = models.TextField(blank=True)
    status      = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE)
    is_popular  = models.BooleanField(default=False)
    tags        = models.ManyToManyField(
        'CategoryTag',
        blank=True,
        related_name='categories',
        db_table='shop_category_tag_assignments',
    )
    gst_rate    = models.ForeignKey(
        'GSTRate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='categories',
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        db_table  = 'shop_categories'
        ordering  = ['name']
        verbose_name_plural = 'categories'

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, Category.objects)
        super().save(*args, **kwargs)


# ─── Product ──────────────────────────────────────────────────
class CategoryTag(models.Model):
    POPULAR_SLUG = 'popular-category'
    TRENDING_SLUG = 'trending-category'
    SYSTEM_SLUGS = (POPULAR_SLUG, TRENDING_SLUG)

    name       = models.CharField(max_length=100, unique=True)
    slug       = models.SlugField(max_length=100, unique=True, blank=True)
    is_system  = models.BooleanField(default=False)
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_category_tags'
        ordering = ['name']

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, CategoryTag.objects)
        if self.slug in self.SYSTEM_SLUGS:
            self.is_system = True
        super().save(*args, **kwargs)

    @classmethod
    def ensure_defaults(cls):
        defaults = [
            ('Popular Category', cls.POPULAR_SLUG),
            ('Trending Category', cls.TRENDING_SLUG),
        ]
        for name, slug in defaults:
            cls.objects.update_or_create(
                slug=slug,
                defaults={'name': name, 'is_system': True, 'is_active': True},
            )


class Product(models.Model):
    class PricingMode(models.TextChoices):
        INCLUSIVE = 'inclusive', 'Tax Inclusive'
        EXCLUSIVE = 'exclusive', 'Tax Exclusive'

    class Status(models.TextChoices):
        AVAILABLE   = 'available',   'Available'
        OUT_OF_STOCK = 'out_of_stock', 'Out of Stock'
        DISCONTINUED = 'discontinued', 'Discontinued'

    name           = models.CharField(max_length=255)
    slug           = models.SlugField(max_length=255, unique=True, blank=True)
    description    = models.TextField()
    price          = models.DecimalField(max_digits=10, decimal_places=2)
    discount_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    category       = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, related_name='products')
    gst_rate       = models.ForeignKey(
        'GSTRate',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products',
    )
    gst_applicable = models.BooleanField(default=True)
    gst_pricing_mode = models.CharField(
        max_length=20,
        choices=PricingMode.choices,
        default=PricingMode.EXCLUSIVE,
    )
    hsn_sac_code   = models.CharField(max_length=20, blank=True, default='')
    stock          = models.PositiveIntegerField(default=0)
    status         = models.CharField(max_length=20, choices=Status.choices, default=Status.AVAILABLE)
    colors         = models.JSONField(default=list, blank=True)   # e.g. ["red","blue","black"]
    created_at     = models.DateTimeField(default=timezone.now)
    updated_at     = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_products'
        ordering = ['-created_at']

    def __str__(self):
        return self.name

    @property
    def discount_percent(self):
        if self.discount_price and self.price > 0:
            return round((1 - float(self.discount_price) / float(self.price)) * 100)
        return 0

    @property
    def average_rating(self):
        reviews = self.reviews.all()
        if reviews.exists():
            return round(sum(r.rating for r in reviews) / reviews.count(), 1)
        return 0.0

    @property
    def review_count(self):
        return self.reviews.count()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = unique_slug(self, self.name, Product.objects)
        super().save(*args, **kwargs)


class GSTRate(models.Model):
    name = models.CharField(max_length=80, unique=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_gst_rates'
        ordering = ['percentage', 'name']

    def __str__(self):
        return f"{self.name} ({self.percentage}%)"

    def clean(self):
        if self.percentage is not None and (self.percentage < 0 or self.percentage > 100):
            raise ValidationError({'percentage': 'GST percentage must be between 0 and 100.'})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def ensure_defaults(cls):
        for pct in ('0.00', '5.00', '12.00', '18.00', '28.00'):
            label = f"GST {pct.rstrip('0').rstrip('.')}%"
            cls.objects.get_or_create(
                percentage=pct,
                defaults={'name': label, 'is_active': True},
            )


class GSTConfig(models.Model):
    class PricingMode(models.TextChoices):
        INCLUSIVE = 'inclusive', 'Inclusive'
        EXCLUSIVE = 'exclusive', 'Exclusive'

    enabled = models.BooleanField(default=True)
    seller_state = models.CharField(max_length=100, default='Tamil Nadu')
    default_rate = models.ForeignKey(
        GSTRate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='default_configs',
    )
    pricing_mode = models.CharField(
        max_length=20,
        choices=PricingMode.choices,
        default=PricingMode.EXCLUSIVE,
    )
    seller_name = models.CharField(max_length=150, default='Kj Apperals')
    seller_gstin = models.CharField(max_length=20, blank=True, default='')
    seller_address = models.TextField(blank=True, default='')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_gst_config'
        verbose_name = 'GST configuration'
        verbose_name_plural = 'GST configuration'

    def __str__(self):
        return 'GST Configuration'

    @classmethod
    def current(cls):
        GSTRate.ensure_defaults()
        default_rate = GSTRate.objects.filter(percentage='18.00').first() or GSTRate.objects.first()
        config, created = cls.objects.get_or_create(
            pk=1,
            defaults={'default_rate': default_rate},
        )
        if not config.default_rate_id and default_rate:
            config.default_rate = default_rate
            config.save(update_fields=['default_rate'])
        return config


# ─── Product Images (gallery) ─────────────────────────────────
class StoreAddressSettings(models.Model):
    sender_name = models.CharField(max_length=150, default='Kj Apperals')
    brand_logo = models.ImageField(upload_to='store/logos/', blank=True, null=True)
    sender_phone = models.CharField(max_length=20, blank=True, default='')
    sender_address = models.TextField(blank=True, default='')
    contact_email = models.EmailField(blank=True, default='')
    contact_phone = models.CharField(max_length=20, blank=True, default='')
    whatsapp_number = models.CharField(max_length=20, blank=True, default='')
    business_hours = models.CharField(max_length=120, blank=True, default='')
    instagram_url = models.URLField(max_length=500, blank=True, default='')
    facebook_url = models.URLField(max_length=500, blank=True, default='')
    pinterest_url = models.URLField(max_length=500, blank=True, default='')
    youtube_url = models.URLField(max_length=500, blank=True, default='')
    x_url = models.URLField(max_length=500, blank=True, default='')
    announcement_enabled = models.BooleanField(default=False)
    announcement_message_1 = models.CharField(max_length=180, blank=True, default='')
    announcement_message_2 = models.CharField(max_length=180, blank=True, default='')
    announcement_message_3 = models.CharField(max_length=180, blank=True, default='')
    cod_enabled = models.BooleanField(default=True)
    cod_extra_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_store_address_settings'
        verbose_name = 'store address settings'
        verbose_name_plural = 'store address settings'

    def __str__(self):
        return 'Store Address Settings'

    @classmethod
    def current(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config


class ProductImage(models.Model):
    product    = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='images')
    image      = models.ImageField(upload_to='products/%Y/%m/')
    is_primary = models.BooleanField(default=False)
    order      = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'shop_product_images'
        ordering = ['order', 'id']

    def save(self, *args, **kwargs):
        # Only one image per product can be primary
        if self.is_primary:
            ProductImage.objects.filter(
                product=self.product, is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)

# ─── Admin auth models ────────────────────────────────────────
class CarouselItem(models.Model):
    class RedirectType(models.TextChoices):
        PAGE = 'page', 'Page'
        PRODUCT = 'product', 'Product'

    class CustomerPage(models.TextChoices):
        HOME = '/', 'Home'
        SHOP = '/#collection', 'Shop'
        NEW_ARRIVALS = '/#new-arrivals', 'New Arrivals'
        COLLECTIONS = '/#collections', 'Collections'
        WISHLIST = '/#wishlist', 'Wishlist'
        CART = '/#cart', 'Cart'
        PROFILE = '/#profile', 'Profile'

    image = models.ImageField(upload_to='carousel/%Y/%m/', help_text='Desktop / PC carousel image.')
    title = models.CharField(max_length=150, blank=True, default='')
    description = models.TextField(max_length=500, blank=True, default='')
    button_text = models.CharField(max_length=50, blank=True, default='')
    redirect_type = models.CharField(
        max_length=10, choices=RedirectType.choices, blank=True, default=''
    )
    customer_page = models.CharField(
        max_length=50, choices=CustomerPage.choices, blank=True
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='carousel_items',
    )
    order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_carousel_items'
        ordering = ['order', 'id']

    def __str__(self):
        return self.title or f'Carousel item {self.pk or ""}'.strip()

    @property
    def has_overlay_content(self):
        return bool(self.title or self.description or self.button_text)

    def clean(self):
        if not self.redirect_type:
            self.customer_page = ''
            self.product = None
            return

        errors = {}
        if self.redirect_type == self.RedirectType.PAGE:
            if not self.customer_page:
                errors['customer_page'] = 'Select a customer page.'
            self.product = None
        elif self.redirect_type == self.RedirectType.PRODUCT:
            if not self.product_id:
                errors['product'] = 'Select a product.'
            self.customer_page = ''
        if errors:
            raise ValidationError(errors)

    @property
    def redirect_url(self):
        if not self.redirect_type:
            return ''
        if self.redirect_type == self.RedirectType.PRODUCT and self.product:
            return f'/products/{self.product.slug}/'
        return self.customer_page or '/'


class AdminUser(models.Model):
    username   = models.CharField(max_length=50, unique=True)
    email      = models.EmailField(unique=True)
    password   = models.CharField(max_length=256)   # bcrypt hash
    is_active  = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'shop_admin_users'

    def __str__(self):
        return self.username


class LoginAttempt(models.Model):
    username     = models.CharField(max_length=50)
    ip_address   = models.GenericIPAddressField()
    attempted_at = models.DateTimeField(auto_now_add=True)
    success      = models.BooleanField(default=False)

    class Meta:
        db_table = 'shop_login_attempts'


class NewsletterSubscription(models.Model):
    email = models.EmailField(unique=True)
    subscribed_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'shop_newsletter_subscriptions'
        ordering = ['-subscribed_at']

    def __str__(self):
        return self.email


class HomeTheme(models.Model):
    primary_color = models.CharField(max_length=7, default='#7c5357')
    background_color = models.CharField(max_length=7, default='#fffdfa')
    dark_background_color = models.CharField(max_length=7, default='#030303')
    sale_background_color = models.CharField(max_length=7, default='#e8b2bb')
    text_color = models.CharField(max_length=7, default='#1a1c1a')
    muted_text_color = models.CharField(max_length=7, default='#504444')
    surface_color = models.CharField(max_length=7, default='#ffffff')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_home_theme'
        verbose_name = 'home theme'
        verbose_name_plural = 'home theme'

    def __str__(self):
        return 'Home theme'

    @classmethod
    def current(cls):
        theme, _ = cls.objects.get_or_create(pk=1)
        return theme


class HomeNewsTicker(models.Model):
    message = models.CharField(max_length=180)
    link_url = models.CharField(max_length=255, blank=True, default='')
    display_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_home_news_tickers'
        ordering = ['display_order', 'id']

    def __str__(self):
        return self.message


class HomeSection(models.Model):
    class SectionType(models.TextChoices):
        CATEGORIES = 'categories', 'Categories'
        PRODUCTS = 'products', 'Products'
        MIXED = 'mixed', 'Categories and Products'

    DEFAULT_SECTIONS = (
        ('Popular Categories', SectionType.CATEGORIES, 10),
        ('Trending Categories', SectionType.CATEGORIES, 20),
        ('New Arrivals', SectionType.PRODUCTS, 30),
        ('Trending Products', SectionType.PRODUCTS, 40),
        ('Featured Products', SectionType.PRODUCTS, 50),
        ('Best Sellers', SectionType.PRODUCTS, 60),
    )

    title = models.CharField(max_length=120)
    section_type = models.CharField(max_length=20, choices=SectionType.choices)
    display_order = models.PositiveIntegerField(default=0)
    is_visible = models.BooleanField(default=True)
    categories = models.ManyToManyField(
        Category,
        through='HomeSectionCategory',
        blank=True,
        related_name='home_sections',
    )
    products = models.ManyToManyField(
        Product,
        through='HomeSectionProduct',
        blank=True,
        related_name='home_sections',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_home_sections'
        ordering = ['display_order', 'id']

    def __str__(self):
        return self.title

    @classmethod
    def ensure_defaults(cls):
        for title, section_type, display_order in cls.DEFAULT_SECTIONS:
            cls.objects.get_or_create(
                title=title,
                defaults={
                    'section_type': section_type,
                    'display_order': display_order,
                    'is_visible': True,
                },
            )


class HomeSectionCategory(models.Model):
    section = models.ForeignKey(HomeSection, on_delete=models.CASCADE, related_name='category_links')
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    item_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'shop_home_section_categories'
        ordering = ['item_order', 'id']
        unique_together = ('section', 'category')


class HomeSectionProduct(models.Model):
    section = models.ForeignKey(HomeSection, on_delete=models.CASCADE, related_name='product_links')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    item_order = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'shop_home_section_products'
        ordering = ['item_order', 'id']
        unique_together = ('section', 'product')


class PasswordResetOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='password_reset_otps')
    otp_code = models.CharField(max_length=16)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        db_table = 'shop_password_reset_otps'
        ordering = ['-created_at']

    def __str__(self):
        return f"OTP for {self.user.email} - {self.otp_code}"


class OTPSettings(models.Model):
    class OTPDigits(models.IntegerChoices):
        FOUR = 4, '4 Digits'
        SIX = 6, '6 Digits'
        EIGHT = 8, '8 Digits'

    company_name = models.CharField(max_length=255, default='Kj Apperals', help_text='Company / Store name displayed in email template header and sender name')
    sender_email = models.EmailField(default='noreply@kjapperals.com', help_text='Sender email address used for sending OTPs')
    smtp_host_user = models.CharField(max_length=255, blank=True, default='', help_text='SMTP Username / Email address (e.g. joellinson00@gmail.com)')
    smtp_host_password = models.CharField(max_length=255, blank=True, default='', help_text='SMTP App Password (16-digit code from Google)')
    otp_length = models.IntegerField(choices=OTPDigits.choices, default=6, help_text='OTP length (4, 6, or 8 digits)')
    otp_expiry_minutes = models.PositiveIntegerField(default=5, help_text='OTP validity in minutes')
    resend_cooldown_seconds = models.PositiveIntegerField(default=60, help_text='Resend cooldown in seconds')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_otp_settings'
        verbose_name = 'OTP settings'
        verbose_name_plural = 'OTP settings'

    def __str__(self):
        return f"OTP Settings ({self.otp_length} digits, {self.otp_expiry_minutes}m expiry)"

    @classmethod
    def current(cls):
        from django.conf import settings
        default_user = getattr(settings, 'EMAIL_HOST_USER', 'joellinson00@gmail.com')
        default_pass = getattr(settings, 'EMAIL_HOST_PASSWORD', 'nazdbvhforfacxzl')

        config, created = cls.objects.get_or_create(
            pk=1,
            defaults={
                'sender_email': default_user or 'joellinson00@gmail.com',
                'smtp_host_user': default_user or '',
                'smtp_host_password': default_pass or '',
                'otp_length': 6,
                'otp_expiry_minutes': 5,
                'resend_cooldown_seconds': 60,
            }
        )
        if not config.smtp_host_user and default_user:
            config.smtp_host_user = default_user
            config.save(update_fields=['smtp_host_user'])
        if not config.smtp_host_password and default_pass:
            config.smtp_host_password = default_pass
            config.save(update_fields=['smtp_host_password'])
        return config


class Favorite(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='favorited_by')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'shop_favorites'
        unique_together = ('user', 'product')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.product.name}"


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    phone = models.CharField(max_length=20, blank=True, default='')
    street_address = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    state = models.CharField(max_length=100, blank=True, default='')
    county = models.CharField(max_length=100, blank=True, default='', help_text='District / County')
    country = models.CharField(max_length=100, blank=True, default='India')
    postal_code = models.CharField(max_length=20, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_user_profiles'

    def __str__(self):
        return f"Profile for {self.user.email}"


# ─── Shipping & Orders ────────────────────────────────────────

class DeliveryZone(models.Model):
    country = models.CharField(max_length=100, default='India')
    state = models.CharField(max_length=100)
    city = models.CharField(max_length=100, blank=True, default='')
    county = models.CharField(max_length=100, blank=True, default='', help_text='County or District')
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=150.00)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'shop_delivery_zones'
        ordering = ['country', 'state', 'city']

    def __str__(self):
        parts = [self.country, self.state]
        if self.county:
            parts.append(self.county)
        if self.city:
            parts.append(self.city)
        return f"{', '.join(parts)} (Rs. {self.delivery_fee})"


class DeliveryConfig(models.Model):
    class FeeType(models.TextChoices):
        FLAT = 'flat', 'Flat Base Fee'
        ZONE = 'zone', 'Zone-based Fee'
        THRESHOLD = 'threshold', 'Free Above Threshold, Else Zone/Flat'

    fee_type = models.CharField(max_length=20, choices=FeeType.choices, default=FeeType.FLAT)
    base_fee = models.DecimalField(max_digits=10, decimal_places=2, default=150.00)
    free_threshold = models.DecimalField(max_digits=10, decimal_places=2, default=2000.00)

    class Meta:
        db_table = 'shop_delivery_config'
        verbose_name = 'delivery configuration'
        verbose_name_plural = 'delivery configuration'

    def __str__(self):
        return 'Delivery Settings'

    @classmethod
    def current(cls):
        config, _ = cls.objects.get_or_create(pk=1)
        return config


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending Payment'
        PROCESSING = 'processing', 'Processing'
        SHIPPED = 'shipped', 'Shipped'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'
        RETURN_REQUESTED = 'return_requested', 'Replacement Requested'
        RETURNED = 'returned', 'Replaced Successfully'

    class PaymentStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'
        TIMEOUT = 'timeout', 'Timeout'
        REFUNDED = 'refunded', 'Refunded'

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    full_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20)
    
    # Address details
    country = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    county = models.CharField(max_length=100, blank=True, default='')
    street_address = models.CharField(max_length=255)
    postal_code = models.CharField(max_length=20)
    
    # Coordinates (for distance validation if saved)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    
    # Pricing fields
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2)
    cod_extra_charge = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    cgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    sgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    igst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    tax_type = models.CharField(max_length=20, blank=True, default='')
    gst_enabled_snapshot = models.BooleanField(default=False)
    seller_state_snapshot = models.CharField(max_length=100, blank=True, default='')
    total = models.DecimalField(max_digits=10, decimal_places=2)
    
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_method = models.CharField(max_length=50, default='Card')
    payment_status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    payment_id = models.CharField(max_length=100, blank=True, default='')
    phonepe_merchant_order_id = models.CharField(max_length=100, blank=True, default='', db_index=True)
    phonepe_order_id = models.CharField(max_length=100, blank=True, default='')
    phonepe_transaction_id = models.CharField(max_length=100, blank=True, default='')
    phonepe_payment_state = models.CharField(max_length=50, blank=True, default='')
    phonepe_response_code = models.CharField(max_length=80, blank=True, default='')

    # Cancellation & Return details
    cancellation_reason = models.TextField(blank=True, default='')
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_requested = models.BooleanField(default=False)

    return_reason = models.TextField(blank=True, default='')
    returned_at = models.DateTimeField(null=True, blank=True)
    return_requested = models.BooleanField(default=False)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    # Coupon details
    coupon = models.ForeignKey('Coupon', on_delete=models.SET_NULL, null=True, blank=True, related_name='orders')
    coupon_code = models.CharField(max_length=50, blank=True, default='')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_orders'
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} by {self.user.email}"

    @property
    def is_within_7_days(self):
        if self.status != self.Status.DELIVERED:
            return False
        deliv_date = self.delivered_at or self.updated_at
        if not deliv_date:
            return False
        from datetime import timedelta
        return (timezone.now() - deliv_date) <= timedelta(days=7)

    @property
    def can_cancel(self):
        return self.status in [self.Status.PENDING, self.Status.PROCESSING] and not self.cancel_requested

    @property
    def can_return(self):
        return self.status == self.Status.DELIVERED and not self.return_requested and self.is_within_7_days


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True)
    product_name = models.CharField(max_length=255) # snapshot name in case product gets deleted
    price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    taxable_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    gst_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0.00)
    gst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    cgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    sgst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    igst_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    hsn_sac_code = models.CharField(max_length=20, blank=True, default='')
    final_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    
    class Meta:
        db_table = 'shop_order_items'

    def __str__(self):
        return f"{self.quantity} x {self.product_name}"


class PaymentTransaction(models.Model):
    class Provider(models.TextChoices):
        PHONEPE = 'phonepe', 'PhonePe'

    class Status(models.TextChoices):
        INITIATED = 'initiated', 'Initiated'
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'
        TIMEOUT = 'timeout', 'Timeout'

    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='payment_transactions')
    provider = models.CharField(max_length=30, choices=Provider.choices, default=Provider.PHONEPE)
    merchant_order_id = models.CharField(max_length=100, unique=True)
    provider_order_id = models.CharField(max_length=100, blank=True, default='')
    provider_transaction_id = models.CharField(max_length=100, blank=True, default='')
    amount_paise = models.PositiveBigIntegerField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    provider_state = models.CharField(max_length=50, blank=True, default='')
    response_code = models.CharField(max_length=80, blank=True, default='')
    redirect_url = models.URLField(max_length=1000, blank=True, default='')
    request_payload = models.JSONField(default=dict, blank=True)
    response_payload = models.JSONField(default=dict, blank=True)
    verified_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_payment_transactions'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.provider} {self.merchant_order_id} ({self.status})"


class PaymentWebhookEvent(models.Model):
    provider = models.CharField(max_length=30, default=PaymentTransaction.Provider.PHONEPE)
    event_id = models.CharField(max_length=150, blank=True, default='')
    payload_hash = models.CharField(max_length=64, unique=True)
    transaction = models.ForeignKey(
        PaymentTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='webhook_events',
    )
    merchant_order_id = models.CharField(max_length=100, blank=True, default='')
    processed = models.BooleanField(default=False)
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'shop_payment_webhook_events'
        ordering = ['-received_at']

    def __str__(self):
        return f"{self.provider} webhook {self.event_id or self.payload_hash[:10]}"


class Review(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='reviews')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    rating = models.PositiveSmallIntegerField(
        choices=[(1, '1 Star'), (2, '2 Stars'), (3, '3 Stars'), (4, '4 Stars'), (5, '5 Stars')],
        default=5
    )
    comment = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_reviews'
        unique_together = ('user', 'product')
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.product.name} ({self.rating} Stars)"


class ContactMessage(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField()
    subject = models.CharField(max_length=255)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'shop_contact_messages'
        ordering = ['-created_at']
        verbose_name = 'Contact Message'
        verbose_name_plural = 'Contact Messages'

    def __str__(self):
        return f"Message from {self.name} - {self.subject}"


class Coupon(models.Model):
    class DiscountType(models.TextChoices):
        PERCENTAGE = 'percentage', 'Percentage (%)'
        FIXED = 'fixed', 'Fixed Amount (Rs.)'

    code = models.CharField(max_length=50, unique=True, help_text="Unique coupon code, e.g. SAVE20")
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices, default=DiscountType.PERCENTAGE)
    discount_value = models.DecimalField(max_digits=10, decimal_places=2, help_text="Percentage value (e.g. 15 for 15%) or Fixed Amount in Rs.")
    min_purchase = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, help_text="Minimum subtotal required to use coupon")
    max_discount = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, help_text="Max discount limit for percentage coupon")
    valid_from = models.DateTimeField(default=timezone.now)
    valid_until = models.DateTimeField(help_text="Expiry date and time")
    usage_limit = models.PositiveIntegerField(null=True, blank=True, help_text="Max times this coupon can be used across all users")
    used_count = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_coupons'
        ordering = ['-created_at']
        verbose_name = 'Coupon'
        verbose_name_plural = 'Coupons'

    def __str__(self):
        if self.discount_type == self.DiscountType.PERCENTAGE:
            return f"{self.code} ({self.discount_value}% Off)"
        return f"{self.code} (Rs. {self.discount_value} Off)"

    @property
    def is_expired(self):
        return timezone.now() > self.valid_until

    @property
    def is_started(self):
        return timezone.now() >= self.valid_from

    @property
    def days_left(self):
        if self.is_expired:
            return 0
        diff = self.valid_until - timezone.now()
        return max(0, diff.days)

    def is_valid(self, subtotal=0.0):
        now = timezone.now()
        if not self.is_active:
            return False, "This coupon code is currently inactive."
        if now < self.valid_from:
            return False, "This coupon code is not active yet."
        if now > self.valid_until:
            return False, "This coupon code has expired."
        if self.usage_limit and self.used_count >= self.usage_limit:
            return False, "This coupon code usage limit has been reached."
        if float(subtotal) < float(self.min_purchase):
            return False, f"Minimum subtotal of Rs. {float(self.min_purchase):.2f} is required for this coupon."
        return True, ""

    def calculate_discount(self, subtotal):
        subtotal = float(subtotal)
        if self.discount_type == self.DiscountType.PERCENTAGE:
            disc = (subtotal * float(self.discount_value)) / 100.0
            if self.max_discount and float(self.max_discount) > 0:
                disc = min(disc, float(self.max_discount))
            return round(min(disc, subtotal), 2)
        else:
            disc = float(self.discount_value)
            return round(min(disc, subtotal), 2)


class PopupBanner(models.Model):
    class DisplayFrequency(models.TextChoices):
        EVERY_VISIT = 'every_visit', 'Every Page Load'
        ONCE_PER_SESSION = 'once_per_session', 'Once Per Session'
        ONCE_PER_DAY = 'once_per_day', 'Once Per Day'

    title = models.CharField(max_length=150, blank=True, default='', help_text="Popup title, e.g. Welcome Special Offer!")
    subtitle = models.TextField(max_length=500, blank=True, default='', help_text="Short description or terms")
    image = models.ImageField(upload_to='popups/%Y/%m/', help_text="Promotional popup photo uploaded by Admin")
    button_text = models.CharField(max_length=50, blank=True, default='Shop Now', help_text="CTA button text")
    link_url = models.CharField(max_length=255, blank=True, default='/shop/', help_text="URL to redirect when clicked")
    display_delay = models.PositiveIntegerField(default=1, help_text="Delay in seconds before popup appears")
    frequency = models.CharField(
        max_length=20,
        choices=DisplayFrequency.choices,
        default=DisplayFrequency.ONCE_PER_SESSION,
        help_text="How often the popup appears to the customer"
    )
    is_active = models.BooleanField(default=True, help_text="Toggle popup active/inactive state")
    show_on_homepage = models.BooleanField(default=True, help_text="Show this popup banner image below Popular Categories on the homepage")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'shop_popup_banners'
        ordering = ['-created_at']
        verbose_name = 'Popup Banner'
        verbose_name_plural = 'Popup Banners'

    def __str__(self):
        return self.title or f"Popup Banner #{self.id}"




