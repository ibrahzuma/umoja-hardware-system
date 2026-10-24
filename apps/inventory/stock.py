"""The one door stock is written through: which godown, and its row.

Stock is counted per godown (see `Godown`). Anything that moves stock names
the godown it moves in; where it names only a branch — the Android app, an
import, a legacy screen — the branch's default godown is meant. Keeping that
rule here means no caller can quietly create a second row for the branch.
"""

from __future__ import annotations

from rest_framework.exceptions import ValidationError

from .models import Godown, Stock


def resolve_godown(branch=None, godown=None):
    """The godown meant: the one given (by object or id), else the branch's
    default. A godown given with a branch must belong to that branch."""
    if godown not in (None, ''):
        if not isinstance(godown, Godown):
            godown = Godown.objects.filter(pk=godown).first()
            if godown is None:
                raise ValidationError({'godown': 'That godown does not exist.'})
        branch_id = getattr(branch, 'pk', branch)
        if branch_id not in (None, '') and int(branch_id) != godown.branch_id:
            raise ValidationError({'godown': f'{godown.name} is not in that branch.'})
        return godown
    if branch in (None, ''):
        raise ValidationError({'branch': 'Say which branch or godown.'})
    return Godown.default_for(branch)


def stock_row(product, branch=None, godown=None, lock=False):
    """The stock row for a product in a godown, made at zero if missing."""
    godown = resolve_godown(branch, godown)
    product_id = getattr(product, 'pk', product)
    qs = Stock.objects.select_for_update() if lock else Stock.objects
    row = qs.filter(product_id=product_id, godown=godown).first()
    if row is None:
        row = Stock.objects.create(product_id=product_id, godown=godown, branch_id=godown.branch_id,
                                   quantity=0)
        if lock:
            row = Stock.objects.select_for_update().get(pk=row.pk)
    return row


def add_stock(product, quantity, branch=None, godown=None):
    """Move `quantity` (negative to take away) into a godown's stock."""
    row = stock_row(product, branch, godown, lock=True)
    row.quantity += int(quantity)
    row.save()
    return row
