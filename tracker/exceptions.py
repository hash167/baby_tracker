"""Errors for optimistic writes.

Messages name the model and primary key only. They do not include field
values, so a log of the exception is not a copy of the audit payload.
"""

from __future__ import annotations


class VersionConflict(Exception):
    """The row changed after the caller read `expected_version`."""

    def __init__(
        self,
        *,
        model_label: str,
        pk,
        expected_version: int,
        current_version: int | None,
    ):
        self.model_label = model_label
        self.pk = pk
        self.expected_version = expected_version
        self.current_version = current_version
        super().__init__(
            f'Version conflict for {model_label} {pk}: '
            f'expected {expected_version}, current is {current_version}.'
        )


class DeletedRecord(Exception):
    """A soft-deleted row must be restored before it can be updated."""

    def __init__(self, *, model_label: str, pk):
        self.model_label = model_label
        self.pk = pk
        super().__init__(f'{model_label} {pk} is deleted.')


class RecordNotDeleted(Exception):
    """restore() was called on a row that is still active."""

    def __init__(self, *, model_label: str, pk):
        self.model_label = model_label
        self.pk = pk
        super().__init__(f'{model_label} {pk} is not deleted.')
