import logging
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.mail import get_connection, EmailMultiAlternatives
from django.template.loader import render_to_string
from .models import OTPSettings, StoreAddressSettings

logger = logging.getLogger(__name__)


def _brand_logo_url():
    store_settings = StoreAddressSettings.current()
    if not store_settings.brand_logo:
        return ''

    logo_url = store_settings.brand_logo.url
    if logo_url.startswith(('http://', 'https://')):
        return logo_url

    base_url = getattr(settings, 'SITE_URL', '').strip().rstrip('/')
    if base_url:
        return f'{base_url}{logo_url}'

    try:
        domain = Site.objects.get_current().domain.strip().rstrip('/')
    except Exception:
        domain = ''

    if not domain:
        return logo_url

    scheme = 'http' if domain.startswith(('localhost', '127.0.0.1')) else 'https'
    return f'{scheme}://{domain}{logo_url}'

EVENT_CONFIG = {
    'placed': {
        'subject_suffix': 'Your Order Has Been Placed! 🛍️',
        'badge_text': 'Order Placed',
        'badge_bg': '#e6f4ea',
        'badge_color': '#137333',
        'headline': 'Thank you for your order!',
        'subtitle': 'We have received your order and are preparing it for shipment.',
        'accent_color': '#137333',
    },
    'shipped': {
        'subject_suffix': 'Your Order Has Been Shipped! 🚚',
        'badge_text': 'Order Shipped',
        'badge_bg': '#e8f0fe',
        'badge_color': '#1a73e8',
        'headline': 'Your order is on the way!',
        'subtitle': 'Great news! Your package has been dispatched and is en route to your delivery address.',
        'accent_color': '#1a73e8',
    },
    'delivered': {
        'subject_suffix': 'Your Order Has Been Delivered! 🎉',
        'badge_text': 'Order Delivered',
        'badge_bg': '#e6f4ea',
        'badge_color': '#188038',
        'headline': 'Your order has arrived!',
        'subtitle': 'Your package has been successfully delivered. We hope you love your purchase!',
        'accent_color': '#188038',
    },
    'cancelled': {
        'subject_suffix': 'Your Order Has Been Cancelled ❌',
        'badge_text': 'Order Cancelled',
        'badge_bg': '#fce8e6',
        'badge_color': '#c5221f',
        'headline': 'Order Cancellation Notice',
        'subtitle': 'Your order has been cancelled.',
        'accent_color': '#c5221f',
    },
    'replacement_requested': {
        'subject_suffix': 'Replacement Request Received 🔄',
        'badge_text': 'Replacement Requested',
        'badge_bg': '#feefc3',
        'badge_color': '#b06000',
        'headline': 'Replacement Request Under Review',
        'subtitle': 'We have received your replacement request and our team is processing it.',
        'accent_color': '#b06000',
    },
    'replacement_completed': {
        'subject_suffix': 'Replacement Completed Successfully! ✅',
        'badge_text': 'Replaced Successfully',
        'badge_bg': '#e6f4ea',
        'badge_color': '#137333',
        'headline': 'Replacement Completed!',
        'subtitle': 'Your replacement request has been fulfilled successfully. Thank you for your patience!',
        'accent_color': '#137333',
    },
}


def send_order_status_email(order, event_type):
    """
    Sends transactional emails to customer on order status changes:
    - placed
    - shipped
    - delivered
    - cancelled
    - replacement_requested
    - replacement_completed
    """
    if event_type not in EVENT_CONFIG:
        logger.warning(f"Unknown email event type '{event_type}' for order #{order.id}")
        return False

    recipient_email = order.user.email or order.user.username
    if not recipient_email or '@' not in recipient_email:
        logger.warning(f"Order #{order.id} has no valid email address ({recipient_email})")
        return False

    config = EVENT_CONFIG[event_type]
    otp_config = OTPSettings.current()
    company = otp_config.company_name or 'Kj Apperals'
    brand_logo_url = _brand_logo_url()

    smtp_user = (otp_config.smtp_host_user or getattr(settings, 'EMAIL_HOST_USER', '')).strip()
    smtp_pass = (otp_config.smtp_host_password or getattr(settings, 'EMAIL_HOST_PASSWORD', '')).strip()
    sender_addr = smtp_user if smtp_user else (otp_config.sender_email or 'webmaster@localhost')
    formatted_from = f"{company} <{sender_addr}>"

    subject = f"{company} - Order #{order.id}: {config['subject_suffix']}"

    # Fetch order items
    items = order.items.all()

    # Render HTML template
    html_content = render_to_string('emails/order_status_email.html', {
        'company_name': company,
        'brand_logo_url': brand_logo_url,
        'order': order,
        'items': items,
        'event_config': config,
        'event_type': event_type,
    })

    gst_text = ''
    if order.gst_enabled_snapshot or order.gst_amount > 0:
        gst_text = (
            f"GST Invoice: INV-{order.id}\n"
            f"Taxable Amount: Rs. {order.taxable_amount}\n"
            f"CGST: Rs. {order.cgst_amount}\n"
            f"SGST: Rs. {order.sgst_amount}\n"
            f"IGST: Rs. {order.igst_amount}\n"
            f"Total GST: Rs. {order.gst_amount}\n"
            f"Seller State: {order.seller_state_snapshot or '-'}\n"
        )

    text_content = (
        f"Hello {order.full_name},\n\n"
        f"{config['headline']}\n"
        f"{config['subtitle']}\n\n"
        f"Order ID: #{order.id}\n"
        f"{gst_text}"
        f"Order Total: ₹{order.total}\n"
        f"Payment Method: {order.payment_method}\n\n"
        f"Thank you for shopping with {company}!"
    )

    try:
        backend_cls = getattr(settings, 'EMAIL_BACKEND', 'django.core.mail.backends.smtp.EmailBackend')
        if smtp_user and smtp_pass and 'locmem' not in backend_cls:
            connection = get_connection(
                backend=backend_cls,
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
                to=[recipient_email],
                connection=connection,
            )
        else:
            msg = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=formatted_from,
                to=[recipient_email],
            )
        msg.attach_alternative(html_content, "text/html")
        msg.send(fail_silently=False)
        logger.info(f"Order #{order.id} '{event_type}' email sent successfully to {recipient_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send '{event_type}' email for order #{order.id} to {recipient_email}: {e}")
        return False
