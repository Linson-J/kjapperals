from django.test import TestCase, Client, override_settings
from django.contrib.auth.models import User
from shop.models import OTPSettings, PasswordResetOTP, AdminUser
import bcrypt
import json

class OTPSystemTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='customer@example.com',
            email='customer@example.com',
            password='oldpassword123'
        )
        
        hashed = bcrypt.hashpw(b'adminpass', bcrypt.gensalt()).decode('utf-8')
        self.admin = AdminUser.objects.create(
            username='admin',
            email='admin@example.com',
            password=hashed
        )

    def test_admin_otp_settings_update(self):
        # Log in as admin
        session = self.client.session
        session['admin_id'] = self.admin.id
        session['admin_authenticated'] = True
        session['admin_name'] = self.admin.username
        session.save()

        # Update OTP settings via POST JSON
        response = self.client.post(
            '/admin-panel/settings/',
            data=json.dumps({
                'sender_email': 'security@kjapperals.com',
                'otp_length': 8,
                'otp_expiry_minutes': 10,
                'resend_cooldown_seconds': 90,
            }),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)
        res_data = response.json()
        self.assertTrue(res_data['success'])

        cfg = OTPSettings.current()
        self.assertEqual(cfg.sender_email, 'security@kjapperals.com')
        self.assertEqual(cfg.otp_length, 8)
        self.assertEqual(cfg.otp_expiry_minutes, 10)
        self.assertEqual(cfg.resend_cooldown_seconds, 90)

    @override_settings(DEBUG=True)
    def test_otp_request_verify_reset_flow_8_digits(self):
        # Configure 8-digit OTP
        cfg = OTPSettings.current()
        cfg.otp_length = 8
        cfg.otp_expiry_minutes = 15
        cfg.resend_cooldown_seconds = 45
        cfg.save()

        # Step 1: Request OTP
        req_resp = self.client.post(
            '/auth/otp/request/',
            data=json.dumps({'email': 'customer@example.com'}),
            content_type='application/json'
        )
        self.assertEqual(req_resp.status_code, 200)
        req_data = req_resp.json()
        self.assertTrue(req_data['success'])
        self.assertEqual(req_data['otp_length'], 8)
        self.assertEqual(req_data['cooldown_seconds'], 45)
        otp_obj = PasswordResetOTP.objects.filter(user=self.user, is_used=False).latest('created_at')
        otp_code = otp_obj.otp_code
        self.assertEqual(len(otp_code), 8)

        # Step 2: Verify OTP
        ver_resp = self.client.post(
            '/auth/otp/verify/',
            data=json.dumps({
                'email': 'customer@example.com',
                'otp_code': otp_code
            }),
            content_type='application/json'
        )
        self.assertEqual(ver_resp.status_code, 200)
        ver_data = ver_resp.json()
        self.assertTrue(ver_data['success'])

        # Step 3a: Verify reusing old password fails (Uniqueness check)
        fail_resp = self.client.post(
            '/auth/otp/reset/',
            data=json.dumps({
                'email': 'customer@example.com',
                'otp_code': otp_code,
                'password': 'oldpassword123',
                'confirm_password': 'oldpassword123'
            }),
            content_type='application/json'
        )
        self.assertEqual(fail_resp.status_code, 400)
        self.assertFalse(fail_resp.json()['success'])

        # Step 3b: Reset Password with Strong Unique Password
        reset_resp = self.client.post(
            '/auth/otp/reset/',
            data=json.dumps({
                'email': 'customer@example.com',
                'otp_code': otp_code,
                'password': 'BrandNewPass123!',
                'confirm_password': 'BrandNewPass123!'
            }),
            content_type='application/json'
        )
        self.assertEqual(reset_resp.status_code, 200)
        reset_data = reset_resp.json()
        self.assertTrue(reset_data['success'])

        # Verify password updated
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('BrandNewPass123!'))
