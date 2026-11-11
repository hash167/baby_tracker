"""Household, baby, shared event, and audit records.

Timestamps are stored in UTC (`USE_TZ`). `Household.timezone` is an IANA
name used only to group a local calendar day. Soft-deleted rows stay in the
table and are hidden by the default manager.

Audit payloads are written to `AuditEntry` and nowhere else. Do not log
`changes`, copy them onto a span, or send them to telemetry.

`Event` is the shared timeline row (occurred/recorded instants, actor,
version, soft delete, optional import reference). Later event kinds add
columns here or subclass this single table. Models that are not timeline
events can subclass `AuditableModel` alone.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.utils import IntegrityError
from django.utils import timezone as dj_timezone

from tracker.audit import diff_snapshots, snapshot
from tracker.days import local_date, local_day_bounds, validate_iana_timezone
from tracker.exceptions import DeletedRecord, RecordNotDeleted, VersionConflict

# Columns a caller cannot change through save(). delete()/restore() own
# deleted_at. recorded_at is set once, when the event is created.
_FROZEN_ON_UPDATE = frozenset(
    {
        'id',
        'created_at',
        'created_by',
        'recorded_at',
        'deleted_at',
        'version',
        'updated_at',
        'updated_by',
    }
)


def _require_actor(actor):
    if getattr(actor, 'pk', None) is None:
        raise TypeError('actor must be a saved user')


def validate_aware_datetime(value):
    if value is None:
        return
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValidationError(
            'Enter a timezone-aware timestamp. Instants are stored in UTC.'
        )


class AuditableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise RuntimeError(
            'Direct update() skips the version check and audit history. '
            'Use save(actor=...), delete(actor=...), or restore(actor=...).'
        )

    def delete(self):
        raise RuntimeError(
            'Direct delete() skips audit history. Use delete(actor=...) on the record.'
        )

    def bulk_create(self, objs, **kwargs):
        raise RuntimeError(
            'bulk_create() skips audit history. Save each record with an actor.'
        )

    def bulk_update(self, objs, fields, **kwargs):
        raise RuntimeError(
            'bulk_update() skips audit history. Save each record with an actor.'
        )


class AuditableManager(models.Manager.from_queryset(AuditableQuerySet)):
    """Every row, including soft-deleted ones."""


class ActiveManager(AuditableManager):
    """Default manager: rows with `deleted_at` unset."""

    def get_queryset(self):
        return super().get_queryset().filter(deleted_at__isnull=True)


def _mutable_fields(model):
    for field in model._meta.concrete_fields:
        if field.primary_key or field.name in _FROZEN_ON_UPDATE:
            continue
        yield field


def _version_check(name: str) -> models.CheckConstraint:
    return models.CheckConstraint(
        condition=models.Q(version__gte=1),
        name=name,
    )


class AuditableModel(models.Model):
    """Versioned, soft-deleted, audited row.

    `objects` hides soft-deleted rows. `all_objects` does not. Forward
    foreign keys use `all_objects`, so a historical event can still see a
    soft-deleted baby. Reverse relations use `objects`.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.PositiveIntegerField(default=1, editable=False)
    created_at = models.DateTimeField(default=dj_timezone.now, editable=False)
    updated_at = models.DateTimeField(default=dj_timezone.now, editable=False)
    deleted_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='+',
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='+',
        editable=False,
    )

    objects = ActiveManager()
    all_objects = AuditableManager()

    class Meta:
        abstract = True
        default_manager_name = 'objects'
        base_manager_name = 'all_objects'

    def save(self, *args, actor=None, expected_version=None, **kwargs):
        if args or kwargs:
            raise TypeError(
                'Use save(actor=..., expected_version=...) so the version '
                'check and audit history stay in effect.'
            )
        _require_actor(actor)
        if self._state.adding:
            if expected_version not in (None, 1):
                raise ValueError('expected_version applies to updates')
            self._insert(actor)
            return
        self._update(actor, expected_version)

    def delete(self, *args, actor=None, expected_version=None, **kwargs):
        if args or kwargs:
            raise TypeError('Use delete(actor=..., expected_version=...).')
        _require_actor(actor)
        model_label = type(self)._meta.label

        def build(current, expected, actor_):
            if current.deleted_at is not None:
                raise DeletedRecord(model_label=model_label, pk=self.pk)
            now = dj_timezone.now()
            return {
                'deleted_at': now,
                'version': expected + 1,
                'updated_by_id': actor_.pk,
                'updated_at': now,
            }

        self._cas_write(
            actor=actor,
            expected_version=expected_version,
            action=AuditEntry.Action.DELETE,
            build_assignments=build,
            active_only=True,
        )
        return (1, {model_label: 1})

    def restore(self, *, actor, expected_version=None):
        _require_actor(actor)
        model_label = type(self)._meta.label

        def build(current, expected, actor_):
            if current.deleted_at is None:
                raise RecordNotDeleted(model_label=model_label, pk=self.pk)
            now = dj_timezone.now()
            return {
                'deleted_at': None,
                'version': expected + 1,
                'updated_by_id': actor_.pk,
                'updated_at': now,
            }

        self._cas_write(
            actor=actor,
            expected_version=expected_version,
            action=AuditEntry.Action.RESTORE,
            build_assignments=build,
            active_only=False,
        )

    def history(self):
        return AuditEntry.objects.for_record(self)

    def _prepare_for_write(self):
        return None

    def _insert(self, actor):
        now = dj_timezone.now()
        self.created_by = actor
        self.updated_by = actor
        self.version = 1
        self.deleted_at = None
        self.created_at = now
        self.updated_at = now
        self._prepare_for_write()
        self.full_clean()
        try:
            with transaction.atomic():
                super().save(force_insert=True)
                AuditEntry.record(
                    self,
                    actor=actor,
                    action=AuditEntry.Action.CREATE,
                    changes=diff_snapshots({}, snapshot(self)),
                )
                self.refresh_from_db()
        except IntegrityError as exc:
            raise ValidationError(
                {'__all__': 'This record conflicts with an existing one.'}
            ) from exc

    def _update(self, actor, expected_version):
        model_label = type(self)._meta.label

        def build(current, expected, actor_):
            if current.deleted_at is not None:
                raise DeletedRecord(model_label=model_label, pk=self.pk)
            self._prepare_for_write()
            assignments = {}
            for field in _mutable_fields(type(self)):
                new_value = getattr(self, field.attname)
                if getattr(current, field.attname) != new_value:
                    assignments[field.attname] = new_value
            if not assignments:
                return None
            self.full_clean()
            now = dj_timezone.now()
            assignments['version'] = expected + 1
            assignments['updated_by_id'] = actor_.pk
            assignments['updated_at'] = now
            return assignments

        self._cas_write(
            actor=actor,
            expected_version=expected_version,
            action=AuditEntry.Action.UPDATE,
            build_assignments=build,
            active_only=True,
        )

    def _cas_write(
        self,
        *,
        actor,
        expected_version,
        action,
        build_assignments,
        active_only,
    ):
        model = type(self)
        if expected_version is None:
            expected_version = self.version
        try:
            with transaction.atomic():
                current = self._lock_current(expected_version)
                assignments = build_assignments(current, expected_version, actor)
                if assignments is None:
                    return
                before = snapshot(current)
                for attname, value in assignments.items():
                    setattr(current, attname, value)
                changes = diff_snapshots(before, snapshot(current))
                queryset = model.all_objects.filter(
                    pk=self.pk,
                    version=expected_version,
                )
                if active_only:
                    queryset = queryset.filter(deleted_at__isnull=True)
                else:
                    queryset = queryset.filter(deleted_at__isnull=False)
                # Base QuerySet.update, not AuditableQuerySet.update: the
                # public helper is blocked so writers go through this method.
                rows = models.QuerySet.update(queryset, **assignments)
                if rows != 1:
                    fresh = (
                        model.all_objects.filter(pk=self.pk)
                        .values_list('version', flat=True)
                        .first()
                    )
                    raise VersionConflict(
                        model_label=model._meta.label,
                        pk=self.pk,
                        expected_version=expected_version,
                        current_version=fresh,
                    )
                AuditEntry.record(
                    self,
                    actor=actor,
                    action=action,
                    changes=changes,
                )
                self.refresh_from_db()
        except IntegrityError as exc:
            raise ValidationError(
                {'__all__': 'This record conflicts with an existing one.'}
            ) from exc

    def _lock_current(self, expected_version):
        model = type(self)
        try:
            current = model.all_objects.select_for_update().get(pk=self.pk)
        except model.DoesNotExist as exc:
            raise VersionConflict(
                model_label=model._meta.label,
                pk=self.pk,
                expected_version=expected_version,
                current_version=None,
            ) from exc
        if current.version != expected_version:
            raise VersionConflict(
                model_label=model._meta.label,
                pk=self.pk,
                expected_version=expected_version,
                current_version=current.version,
            )
        return current


class Household(AuditableModel):
    """A family that shares one timeline. `timezone` is the display zone."""

    name = models.CharField(max_length=200)
    timezone = models.CharField(
        max_length=64,
        validators=[validate_iana_timezone],
        help_text=(
            'IANA time zone name used to group events into local days. '
            'Instants are stored in UTC.'
        ),
    )

    class Meta:
        constraints = [_version_check('household_version_gte_1')]

    def __str__(self):
        return self.name

    def local_day_bounds(self, day):
        return local_day_bounds(day, self.timezone)

    def local_date(self, instant):
        return local_date(instant, self.timezone)

    def events_on(self, day):
        """Active events whose instant falls on `day` in this household's zone."""
        start, end = self.local_day_bounds(day)
        return self.events.filter(
            deleted_at__isnull=True,
            occurred_at__gte=start,
            occurred_at__lt=end,
        )


class BabyProfile(AuditableModel):
    """A baby in a household. A household can have more than one."""

    household = models.ForeignKey(
        Household,
        on_delete=models.PROTECT,
        related_name='babies',
    )
    name = models.CharField(max_length=200)

    class Meta:
        constraints = [_version_check('baby_version_gte_1')]

    def __str__(self):
        return self.name


class Event(AuditableModel):
    """Shared timeline event. Instants are UTC. `recorded_at` is server-set."""

    household = models.ForeignKey(
        Household,
        on_delete=models.PROTECT,
        related_name='events',
    )
    baby = models.ForeignKey(
        BabyProfile,
        on_delete=models.PROTECT,
        related_name='events',
    )

    class Kind(models.TextChoices):
        EVENT = 'event', 'event'
        DIAPER = 'diaper', 'diaper'
        FEED = 'feed', 'feed'
        SLEEP = 'sleep', 'sleep'
        GROWTH = 'growth', 'growth'
        MEDICAL = 'medical', 'medical'
        UNKNOWN = 'unknown', 'unknown'

    class DiaperType(models.TextChoices):
        WET = 'wet', 'wet'
        DIRTY = 'dirty', 'dirty'
        BOTH = 'both', 'both'
        DRY = 'dry', 'dry'

    class FeedState(models.TextChoices):
        ACTIVE = 'active', 'active'
        PAUSED = 'paused', 'paused'
        FINISHED = 'finished', 'finished'

    class FeedSource(models.TextChoices):
        TIMER = 'timer', 'timer'
        MANUAL = 'manual', 'manual'
        IMPORT = 'import', 'import'

    class Side(models.TextChoices):
        LEFT = 'left', 'left'
        RIGHT = 'right', 'right'

    occurred_at = models.DateTimeField(validators=[validate_aware_datetime])
    recorded_at = models.DateTimeField(validators=[validate_aware_datetime])
    source_system = models.CharField(max_length=64, blank=True, default='')
    source_activity_id = models.CharField(max_length=128, blank=True, default='')
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.EVENT)
    diaper_type = models.CharField(max_length=16, blank=True, default='')
    notes = models.CharField(max_length=2000, blank=True, default='')
    feed_state = models.CharField(max_length=16, blank=True, default='')
    feed_source = models.CharField(max_length=16, blank=True, default='')
    feed_ended_at = models.DateTimeField(
        null=True,
        blank=True,
        validators=[validate_aware_datetime],
    )
    left_seconds = models.IntegerField(null=True, blank=True)
    right_seconds = models.IntegerField(null=True, blank=True)
    start_side = models.CharField(max_length=8, blank=True, default='')
    end_side = models.CharField(max_length=8, blank=True, default='')
    totals_overridden = models.BooleanField(default=False)
    attributed_name = models.CharField(max_length=200, blank=True, default='')
    attributed_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='+',
    )
    import_batch = models.ForeignKey(
        'tracker.ImportBatch',
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name='events',
    )
    import_fingerprint = models.CharField(max_length=64, blank=True, default='')
    photo_names = models.JSONField(default=list, blank=True)
    archive = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            _version_check('event_version_gte_1'),
            models.UniqueConstraint(
                fields=['household', 'source_system', 'source_activity_id'],
                condition=~models.Q(source_activity_id=''),
                name='event_source_activity_uq',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(source_activity_id='') | ~models.Q(source_system='')
                ),
                name='event_source_system_set',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(left_seconds__isnull=True) | models.Q(left_seconds__gte=0)
                ),
                name='event_left_seconds_nonneg',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(right_seconds__isnull=True)
                    | models.Q(right_seconds__gte=0)
                ),
                name='event_right_seconds_nonneg',
            ),
            models.UniqueConstraint(
                fields=['baby'],
                condition=models.Q(
                    kind='feed',
                    deleted_at__isnull=True,
                    feed_state__in=['active', 'paused'],
                ),
                name='one_open_feed_per_baby',
            ),
        ]
        indexes = [
            models.Index(
                fields=['household', 'occurred_at'],
                name='event_active_hh_time',
                condition=models.Q(deleted_at__isnull=True),
            ),
        ]

    def __str__(self):
        return f'Event {self.pk}'

    def _prepare_for_write(self):
        if self.recorded_at is None and self._state.adding:
            self.recorded_at = dj_timezone.now()
        if not self.baby_id:
            return
        baby_household_id = (
            BabyProfile.all_objects.filter(pk=self.baby_id)
            .values_list('household_id', flat=True)
            .first()
        )
        if baby_household_id is None:
            raise ValidationError({'baby': 'Baby does not exist.'})
        if self.household_id and self.household_id != baby_household_id:
            raise ValidationError(
                {'household': 'Baby belongs to a different household.'}
            )
        self.household_id = baby_household_id

    def clean(self):
        super().clean()
        for field_name in ('left_seconds', 'right_seconds'):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValidationError({field_name: 'Duration cannot be negative.'})


class AuditEntryQuerySet(models.QuerySet):
    def for_record(self, record):
        return self.filter(
            content_type=ContentType.objects.get_for_model(type(record)),
            object_id=str(record.pk),
        ).order_by('at', 'pk')


class AuditEntry(models.Model):
    """Who changed a record, when, and which fields moved.

    `changes` is a JSON object of `{field: {before, after}}` stored in the
    database. It is not written to logs or telemetry.
    """

    class Action(models.TextChoices):
        CREATE = 'create', 'create'
        UPDATE = 'update', 'update'
        DELETE = 'delete', 'delete'
        RESTORE = 'restore', 'restore'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT)
    object_id = models.CharField(max_length=36)
    content_object = GenericForeignKey('content_type', 'object_id')
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='audit_entries',
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    at = models.DateTimeField(default=dj_timezone.now)
    changes = models.JSONField()

    objects = AuditEntryQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(
                fields=['content_type', 'object_id', 'at'],
                name='audit_record_at_idx',
            ),
        ]

    def __str__(self):
        return f'Audit {self.action} {self.content_type_id} {self.object_id}'

    def __repr__(self):
        return (
            f'<AuditEntry {self.action} content_type={self.content_type_id} '
            f'object_id={self.object_id} actor={self.actor_id}>'
        )

    @classmethod
    def record(cls, record, *, actor, action, changes):
        # Insert the payload. Do not log `changes`.
        return cls.objects.create(
            content_type=ContentType.objects.get_for_model(type(record)),
            object_id=str(record.pk),
            actor=actor,
            action=action,
            at=dj_timezone.now(),
            changes=changes,
        )


# Imported for Django model discovery. Lives at the bottom so Event and
# AuditEntry exist before the related tables are defined.
from tracker import extra_models as _extra_models  # noqa: E402, F401
