# OxaPay in-Telegram checkout implementation

The bot uses OxaPay for real-money/crypto payment processing. The OxaPay invoice and webhook remain unchanged, so existing conversion, order tracking, automatic plan fulfillment, and payment accounting are preserved.

When an invoice is created, the bot now sends a Telegram `web_app` button instead of a normal URL button. Telegram opens the OxaPay `payment_url` in its in-app WebView, keeping the checkout inside Telegram's interface rather than launching the user's external browser. OxaPay still confirms the payment through `/oxapay-webhook`; the bot grants the plan only after that confirmation.

This is not Telegram Stars and does not use `currency="XTR"`, `pre_checkout_query`, or `successful_payment`. It also does not create a second payment provider. The same WebView behavior is applied to automatic plan purchases and downloadable product purchases.

Operational requirements are that OxaPay returns a valid HTTPS `payment_url`, the bot's configured `public_url` is reachable by OxaPay, and the OxaPay webhook is configured/reachable. If a particular OxaPay checkout page refuses to render in a Telegram client, the fallback is to use a normal URL button for that client; Telegram Web Apps cannot force a third-party provider to allow embedded rendering.

The `/oxapay-webhook` route now validates the raw request body with the `HMAC` header and the configured OxaPay merchant key using HMAC-SHA512. It acknowledges intermediate `paying` callbacks without fulfillment, matches paid callbacks to the recorded `track_id` and `order_id`, records the callback, and prevents an already-approved callback from granting access a second time. The callback contract and signature rules are documented by OxaPay at https://docs.oxapay.com/webhook.

The Mini App now calls `/telegram-payment-mini-app/status` with the authenticated Telegram `init_data` and invoice `track_id`. After the user opens checkout, the frontend polls every three seconds, displays a waiting or connection state, and stops with a success message and Telegram haptic feedback only when the webhook has changed the invoice to `approved`. Status requests are scoped to the authenticated Telegram user and never expose another user's payment.

Automatic plan checkout now shows only the Telegram Web App button. The previous external `Pay Now` URL fallback was removed. If no HTTPS `public_url` is configured, the bot stops before creating an invoice and tells the administrator to configure the public URL instead of showing a redirect button.
