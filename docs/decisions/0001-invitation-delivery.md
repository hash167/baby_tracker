# Invitation delivery and account recovery

## Decision

Accounts are created only from an admin-generated, single-use, expiring link. The link is shown once in the admin screen and shared by the admin. There is no transactional email provider in this version.

Someone who cannot sign in asks an admin. The admin can issue a new invite for an address that does not already have an account, or sit with the person and use **Change password** after they are signed in on a trusted device. Password storage and sessions use Django's auth framework. The invite token is a `django.core.signing` value and is not stored on the invitation row.

## Why

The product requirement is invite-only accounts, and no mail provider is assumed to be configured. The issue names admin-shared one-time links as the fallback until a provider is chosen. This decision is that fallback, adopted so the app can run without an external mail account.

## Later

A mail provider can send the same link and a password-reset link. That change should keep the single-use expiry checks and should still avoid logging the token.
