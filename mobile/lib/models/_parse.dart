/// JSON coercion helpers. DRF serializes `DecimalField` as a string
/// ("170000.00") even though it looks numeric, so a plain `as num?` cast
/// throws TypeError. These helpers accept null, num, or numeric String.

double asDouble(Object? v, [double fallback = 0]) {
  if (v == null) return fallback;
  if (v is num) return v.toDouble();
  return double.tryParse(v.toString()) ?? fallback;
}

int asInt(Object? v, [int fallback = 0]) {
  if (v == null) return fallback;
  if (v is num) return v.toInt();
  return int.tryParse(v.toString()) ?? fallback;
}
