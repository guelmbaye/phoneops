"""Recovery templates.

Only `carrier_cancellation` is required for the Grand Prize MVP (DOCUMENT 02 §29);
the others exist to prove the engine is not logistics-specific and are inert
until a caller passes them explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain import enums


@dataclass(frozen=True)
class ConstraintTemplate:
    key: str
    label: str
    operator: str
    value_type: str
    mandatory: bool = True
    default_required_value: object | None = None


@dataclass(frozen=True)
class RecoveryTemplate:
    exception_type: str
    objective_template: str
    strategy_type: str
    target_role: str
    constraints: list[ConstraintTemplate]
    information_needs: list[tuple[str, str, str]] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    protected_outcome: str = ""

    def question_set(
        self, target: str, entity_ref: str, cutoff: str, day: str = "today"
    ) -> list[str]:
        """Render the questions CALL-E will actually ask someone.

        `day` is not decoration: "Can you collect this today?" asked about a
        window that closes tomorrow gets an answer about the wrong day, from a
        real person, on a real phone call.
        """
        return [
            q.format(target=target, entity=entity_ref, cutoff=cutoff, day=day)
            for q in self.questions
        ]

    def need_set(self, target: str, day: str = "today") -> list[tuple[str, str, str]]:
        return [
            (key, text.format(target=target, day=day), fact)
            for key, text, fact in self.information_needs
        ]


CARRIER_CANCELLATION = RecoveryTemplate(
    exception_type=enums.ExceptionType.CARRIER_CANCELLATION,
    objective_template=(
        "Secure a replacement carrier able to collect {entity} before {cutoff} "
        "with sufficient capacity."
    ),
    strategy_type="replacement_carrier",
    target_role="carrier",
    protected_outcome="Tonight's departure",
    constraints=[
        ConstraintTemplate(
            key="pickup_time",
            label="Pickup must occur before the warehouse cutoff",
            operator=enums.ConstraintOperator.LTE,
            value_type=enums.ValueType.TIME,
        ),
        ConstraintTemplate(
            key="capacity_ok",
            label="Carrier must handle the full shipment",
            operator=enums.ConstraintOperator.EQ,
            value_type=enums.ValueType.BOOLEAN,
            default_required_value=True,
        ),
        ConstraintTemplate(
            key="available",
            label="Carrier must be available on the pickup day",
            operator=enums.ConstraintOperator.EQ,
            value_type=enums.ValueType.BOOLEAN,
            default_required_value=True,
        ),
    ],
    information_needs=[
        ("available", "Can {target} collect the shipment {day}?", "available"),
        ("pickup_time", "What is {target}'s earliest pickup time?", "pickup_time"),
        ("capacity_ok", "Can {target} handle the full shipment?", "capacity_ok"),
    ],
    questions=[
        "Can you collect {entity} {day}?",
        "What is your earliest pickup time?",
        "Can you handle the full shipment?",
        "Can you confirm a pickup before {cutoff}?",
    ],
)

SUPPLIER_CAPACITY_LOSS = RecoveryTemplate(
    exception_type=enums.ExceptionType.SUPPLIER_CAPACITY_LOSS,
    objective_template="Secure {entity} volume from an alternative supplier before {cutoff}.",
    strategy_type="alternative_supplier",
    target_role="supplier",
    protected_outcome="Production continuity",
    constraints=[
        ConstraintTemplate(
            key="quantity",
            label="Supplier must cover the required quantity",
            operator=enums.ConstraintOperator.GTE,
            value_type=enums.ValueType.NUMBER,
        ),
        ConstraintTemplate(
            key="delivery_time",
            label="Delivery must occur before the production cutoff",
            operator=enums.ConstraintOperator.LTE,
            value_type=enums.ValueType.TIME,
        ),
    ],
    information_needs=[
        ("quantity", "How many units can {target} supply?", "quantity"),
        ("delivery_time", "When can {target} deliver?", "delivery_time"),
    ],
    questions=[
        "How many units of {entity} can you supply {day}?",
        "What is your earliest delivery time?",
    ],
)

TECHNICIAN_UNAVAILABLE = RecoveryTemplate(
    exception_type=enums.ExceptionType.TECHNICIAN_UNAVAILABLE,
    objective_template="Secure a technician able to restore {entity} before {cutoff}.",
    strategy_type="alternative_technician",
    target_role="technician",
    protected_outcome="Equipment uptime",
    constraints=[
        ConstraintTemplate(
            key="arrival_time",
            label="Technician must arrive before the restore deadline",
            operator=enums.ConstraintOperator.LTE,
            value_type=enums.ValueType.TIME,
        ),
        ConstraintTemplate(
            key="available",
            label="Technician must be available on the service day",
            operator=enums.ConstraintOperator.EQ,
            value_type=enums.ValueType.BOOLEAN,
            default_required_value=True,
        ),
    ],
    information_needs=[
        ("available", "Is {target} available {day}?", "available"),
        ("arrival_time", "When can {target} arrive on site?", "arrival_time"),
    ],
    questions=["Are you available {day}?", "What is your earliest arrival time on site?"],
)

TEMPLATES: dict[str, RecoveryTemplate] = {
    t.exception_type: t
    for t in (CARRIER_CANCELLATION, SUPPLIER_CAPACITY_LOSS, TECHNICIAN_UNAVAILABLE)
}


def get_template(exception_type: str) -> RecoveryTemplate:
    return TEMPLATES.get(exception_type, CARRIER_CANCELLATION)
