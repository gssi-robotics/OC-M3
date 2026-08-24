from .agriculture import SCENARIO as AGRICULTURE
from .cleaning import SCENARIO as CLEANING
from .order_management import SCENARIO as ORDER_MANAGEMENT

SCENARIOS = {s.name: s for s in (AGRICULTURE, CLEANING, ORDER_MANAGEMENT)}
