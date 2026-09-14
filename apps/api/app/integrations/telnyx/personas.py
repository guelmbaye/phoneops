"""What each demo number says when CALL-E calls it."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass(frozen=True)
class CarrierPersona:
    name: str
    pickup_time: str
    available: bool = True
    capacity_ok: bool = True

    def spoken_line(self) -> str:
        """SSML, with a pause before speaking.

        CALL-E greets the moment the line is answered. Without the break both
        sides talk at once and the pickup time — the one fact the whole demo
        turns on — is the part that gets lost.
        """
        if not self.available:
            return (
                '<speak><break time="3s"/>'
                f"Hello, this is {self.name} operations. "
                "I am sorry, we have no driver available today."
                "</speak>"
            )
        spoken = _as_spoken_clock(self.pickup_time)
        # One short sentence per question. Bundling availability and capacity
        # into a single clause cost the availability field on the first live
        # call: CALL-E extracted the capacity and returned `unknown` for the
        # other, which on an unlucky run triggers a clarification call — another
        # fourteen credits to learn something already said.
        return (
            '<speak><break time="3s"/>'
            f"Hello, this is {self.name} operations. "
            # Naming the carrier in the availability sentence, rather than
            # saying "we", gave CALL-E the clearest signal for that field —
            # it was the one it returned as `unknown` on the first live call.
            f"Yes. {self.name} is available today. "
            "We have capacity for the full shipment. "
            f"Our earliest pickup time is {spoken}. "
            "There is no extra charge, our standard rate applies. "
            f"To confirm, {spoken}."
            "</speak>"
        )


def _as_spoken_clock(clock: str) -> str:
    """ "18:00" -> "6 PM"; "16:45" -> "4 45 PM".

    A synthesiser reading "18:00" says "eighteen hundred", which transcribes
    unreliably. Saying it the way a dispatcher would is what CALL-E hears best.
    """
    try:
        hour, minute = (int(part) for part in clock.split(":"))
    except ValueError:
        return clock
    suffix = "A M" if hour < 12 else "P M"
    spoken_hour = hour % 12 or 12
    if minute == 0:
        return f"{spoken_hour} {suffix}"
    return f"{spoken_hour} {minute:02d} {suffix}"


def _registry() -> dict[str, CarrierPersona]:
    """Number -> persona, from the same settings the mock personas use, so the
    live demo and the offline one tell the same story."""
    mapping: dict[str, CarrierPersona] = {}
    if settings.DEMO_CARRIER_B_PHONE:
        mapping[_key(settings.DEMO_CARRIER_B_PHONE)] = CarrierPersona(
            "Carrier B", settings.DEMO_CARRIER_B_PICKUP
        )
    if settings.DEMO_CARRIER_C_PHONE:
        mapping[_key(settings.DEMO_CARRIER_C_PHONE)] = CarrierPersona("Carrier C", "16:45")
    if settings.DEMO_CARRIER_D_PHONE:
        mapping[_key(settings.DEMO_CARRIER_D_PHONE)] = CarrierPersona(
            "Carrier D", "", available=False, capacity_ok=False
        )
    return mapping


def _key(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def persona_for(called_number: str | None) -> CarrierPersona | None:
    """Which carrier a dialled number plays, or None if it is not a demo line."""
    if not called_number:
        return None
    return _registry().get(_key(called_number))
