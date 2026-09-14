"""Controlled PSTN endpoints that play the demo carriers.

This is a **test harness**, not part of the product. It exists so CALL-E has a
real number to dial that answers with a known line, making the recovery scenario
reproducible without asking two people to sit by a phone.

It is deliberately kept at arm's length from the recovery engine:

- it is mounted only when `TELNYX_SIM_ENABLED` is set;
- it holds no reference to missions, evidence, constraints or strategies;
- it cannot write to the database.

That isolation is the point. The carrier's answer must reach PHONEOPS the long
way — spoken over the telephone network, heard by CALL-E, extracted into a
structured result — or CALL-E stops being necessary and the whole argument
collapses. A shortcut from here into the mission would be indistinguishable
from hard-coding the demo.
"""

from app.integrations.telnyx.personas import CarrierPersona, persona_for

__all__ = ["CarrierPersona", "persona_for"]
