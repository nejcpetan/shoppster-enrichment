"""
Normalization utilities — v2

Handles unit normalization for EnrichedField and the new DimensionSet structure.
"""

from schemas import EnrichedField, DimensionSet


def normalize_to_cm(value: float, unit: str) -> float:
    """Convert any length to centimeters."""
    if value is None: return None
    unit = unit.lower().strip() if unit else ""
    conversions = {
        "mm": 0.1, "millimeter": 0.1, "millimeters": 0.1,
        "cm": 1.0, "centimeter": 1.0, "centimeters": 1.0,
        "m": 100.0, "meter": 100.0, "meters": 100.0,
        "in": 2.54, "inch": 2.54, "inches": 2.54, '"': 2.54,
        "ft": 30.48, "foot": 30.48, "feet": 30.48,
    }
    factor = conversions.get(unit, 1.0)
    return round(float(value) * factor, 2)


def normalize_to_kg(value: float, unit: str) -> float:
    """Convert any weight to kilograms."""
    if value is None: return None
    unit = unit.lower().strip() if unit else ""
    conversions = {
        "g": 0.001, "gram": 0.001, "grams": 0.001,
        "kg": 1.0, "kilogram": 1.0, "kilograms": 1.0,
        "lb": 0.4536, "lbs": 0.4536, "pound": 0.4536, "pounds": 0.4536,
        "oz": 0.02835, "ounce": 0.02835, "ounces": 0.02835,
    }
    factor = conversions.get(unit, 1.0)
    return round(float(value) * factor, 3)


def normalize_to_liters(value: float, unit: str) -> float:
    """Convert any volume to liters."""
    if value is None: return None
    unit = unit.lower().strip() if unit else ""
    conversions = {
        "ml": 0.001, "milliliter": 0.001, "milliliters": 0.001,
        "cl": 0.01,
        "dl": 0.1,
        "l": 1.0, "liter": 1.0, "liters": 1.0, "litre": 1.0, "litres": 1.0,
        "gal": 3.785, "gallon": 3.785,
        "qt": 0.9464, "quart": 0.9464,
        "fl_oz": 0.02957, "fluid ounce": 0.02957,
    }
    factor = conversions.get(unit, 1.0)
    return round(float(value) * factor, 3)


def normalize_field(field: EnrichedField, target_type: str) -> EnrichedField:
    """
    Takes an EnrichedField, normalizes value, updates unit, keeps original in notes.
    target_type: 'length', 'weight', 'volume'
    """
    if not field or field.value is None or not field.unit:
        return field

    try:
        original_val = field.value
        original_unit = field.unit
        new_val = None
        new_unit = None

        if target_type == 'length':
            new_val = normalize_to_cm(original_val, original_unit)
            new_unit = "cm"
        elif target_type == 'weight':
            new_val = normalize_to_kg(original_val, original_unit)
            new_unit = "kg"
        elif target_type == 'volume':
            new_val = normalize_to_liters(original_val, original_unit)
            new_unit = "L"

        new_field = field.model_copy()
        new_field.value = new_val
        new_field.unit = new_unit

        note = f"Normalized from {original_val} {original_unit}"
        if new_field.notes:
            new_field.notes += f"; {note}"
        else:
            new_field.notes = note

        return new_field

    except Exception as e:
        print(f"Normalization failed for {field}: {e}")
        return field


def normalize_dimension_set(dim_set: DimensionSet) -> int:
    """
    Normalize all fields in a DimensionSet in-place.
    Returns the count of fields that were normalized.
    """
    count = 0

    # Length fields (to cm)
    for f_name in ['height', 'length', 'width', 'depth', 'diameter']:
        field = getattr(dim_set, f_name, None)
        if field and field.value is not None and field.unit:
            normalized = normalize_field(field, 'length')
            setattr(dim_set, f_name, normalized)
            count += 1

    # Weight (to kg)
    if dim_set.weight and dim_set.weight.value is not None and dim_set.weight.unit:
        dim_set.weight = normalize_field(dim_set.weight, 'weight')
        count += 1

    # Volume (to L)
    if dim_set.volume and dim_set.volume.value is not None and dim_set.volume.unit:
        dim_set.volume = normalize_field(dim_set.volume, 'volume')
        count += 1

    return count
