"""A test that fails on purpose.

CI runs this in a job marked ``continue-on-error``, so the run stays green, and the next job points
ci-failure-explainer at the real, failed job and checks what it says.
"""


def total(items, discount=0.0):
    return sum(items)  # forgets to apply the discount


def test_total_discount():
    assert total([5, 95], discount=0.1) == 90
