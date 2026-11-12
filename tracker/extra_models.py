"""Membership, invites, feed segments, and import batches.

These are registered by importing this module at the bottom of
`tracker.models`. Audit payloads stay in the database.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone as dj_timezone

from tracker.models import AuditableModel, BabyProfile, Event, Household


class Role(models.TextChoices):
    ADMIN = 'admin', 'admin'
    PARENT = 'parent', 'parent'
    CAREGIVER = 'caregiver', 'caregiver'


class Membership(AuditableModel):
    """A user's role in one household."""

    household = models.ForeignKey(
        Household,
        on_delete=models.PROTECT,
        related_name='memberships',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='memberships',
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['household', 'user'],
                name='one_membership_per_household_user',
            ),
        ]

    def __str__(self):
        return f'Membership {self.pk}'


class Invitation(AuditableModel):
    """Single-use invite. The signed token is not stored on the row."""

    household = models.ForeignKey(
        Household,
        on_delete=models.PROTECT,
        related_name='invitations',
    )
    email = models.EmailField()
    role = models.CharField(max_length=16, choices=Role.choices)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['household', 'email'], name='invite_household_email'),
        ]

    def __str__(self):
        return f'Invitation {self.pk}'

    @property
    def is_open(self) -> bool:
        return self.accepted_at is None and self.expires_at > dj_timezone.now()


class AccountControl(models.Model):
    """Session generation for one user. Bumping it revokes every session."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='account_control',
    )
    generation = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f'AccountControl {self.user_id}'


class TrackedSession(models.Model):
    """Maps a Django session key to a user so it can be deleted."""

    session_key = models.CharField(max_length=40, unique=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='tracked_sessions',
    )
    created_at = models.DateTimeField(default=dj_timezone.now)

    def __str__(self):
        return f'TrackedSession {self.pk}'


class LoginThrottle(models.Model):
    """Failed-login window. `subject_hash` is not the username."""

    subject_hash = models.CharField(max_length=64, unique=True)
    failures = models.PositiveIntegerField(default=0)
    window_started = models.DateTimeField(default=dj_timezone.now)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f'LoginThrottle {self.pk}'


class RequestKey(models.Model):
    """Idempotency key for one actor. A retry returns `event` and does not reapply."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='request_keys',
    )
    scope = models.CharField(max_length=32)
    key = models.CharField(max_length=80)
    action = models.CharField(max_length=32, blank=True, default='')
    event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='request_keys',
    )
    created_at = models.DateTimeField(default=dj_timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['actor', 'scope', 'key'],
                name='request_key_once',
            ),
        ]

    def __str__(self):
        return f'RequestKey {self.pk}'


class FeedingSegment(models.Model):
    """One side of a timer feed. Manual and imported feeds have no segments."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name='segments',
    )
    side = models.CharField(max_length=8, choices=Event.Side.choices)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['session'],
                condition=models.Q(ended_at__isnull=True),
                name='one_open_segment_per_session',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(ended_at__isnull=True)
                    | models.Q(ended_at__gte=models.F('started_at'))
                ),
                name='segment_end_not_before_start',
            ),
        ]

    def __str__(self):
        return f'FeedingSegment {self.pk}'


class ImportBatch(AuditableModel):
    """Staged Nara CSV. Live events are written only on commit."""

    class Status(models.TextChoices):
        STAGED = 'staged', 'staged'
        COMMITTED = 'committed', 'committed'
        ROLLED_BACK = 'rolled_back', 'rolled_back'
        ABANDONED = 'abandoned', 'abandoned'

    household = models.ForeignKey(
        Household,
        on_delete=models.PROTECT,
        related_name='import_batches',
    )
    baby = models.ForeignKey(
        BabyProfile,
        on_delete=models.PROTECT,
        related_name='import_batches',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.STAGED,
    )
    original_name = models.CharField(max_length=255, blank=True, default='')
    stored_name = models.CharField(max_length=255, blank=True, default='')
    purge_after = models.DateTimeField(null=True, blank=True)
    purged_at = models.DateTimeField(null=True, blank=True)
    profile = models.JSONField(null=True, blank=True)
    name_map = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'ImportBatch {self.pk}'


class ImportFile(models.Model):
    """Original CSV bytes in PostgreSQL.

    Not audited: a snapshot would copy the file into audit JSON. Any app
    replica can read this row, so uploads do not need a node-local volume.
    """

    batch = models.OneToOneField(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name='upload',
    )
    content = models.BinaryField()

    def __str__(self):
        return f'ImportFile {self.batch_id}'


class ImportRow(models.Model):
    """One parsed CSV row. `raw` and `mapped` stay in the database."""

    class Classification(models.TextChoices):
        OK = 'ok', 'ok'
        WARNING = 'warning', 'warning'
        ERROR = 'error', 'error'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name='rows',
    )
    row_number = models.PositiveIntegerField()
    record_type = models.CharField(max_length=64, blank=True, default='')
    source_activity_id = models.CharField(max_length=128, blank=True, default='')
    classification = models.CharField(max_length=16, choices=Classification.choices)
    disposition = models.CharField(max_length=32, blank=True, default='')
    decision = models.CharField(max_length=16, blank=True, default='pending')
    warnings = models.JSONField(default=list, blank=True)
    mapped = models.JSONField(default=dict, blank=True)
    raw = models.JSONField(default=dict, blank=True)
    fingerprint = models.CharField(max_length=64, blank=True, default='')
    event = models.ForeignKey(
        Event,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='import_rows',
    )

    class Meta:
        ordering = ['row_number']
        constraints = [
            models.UniqueConstraint(
                fields=['batch', 'row_number'],
                name='import_row_number_once',
            ),
        ]

    def __str__(self):
        return f'ImportRow {self.batch_id} #{self.row_number}'


class ImportLink(models.Model):
    """What a commit did to one event, so rollback can undo exactly that."""

    class Action(models.TextChoices):
        CREATED = 'created', 'created'
        UPDATED = 'updated', 'updated'
        RESTORED = 'restored', 'restored'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name='links',
    )
    event = models.ForeignKey(
        Event,
        on_delete=models.PROTECT,
        related_name='import_links',
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    preimage = models.JSONField(default=dict, blank=True)
    imported_version = models.PositiveIntegerField()
    rollback_choice = models.CharField(max_length=16, blank=True, default='')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['batch', 'event'],
                name='import_link_once',
            ),
        ]

    def __str__(self):
        return f'ImportLink {self.pk}'
