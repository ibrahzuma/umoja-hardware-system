class Product {
  Product({
    required this.id,
    required this.name,
    required this.sku,
    required this.price,
    required this.cost,
    required this.weight,
    required this.productType,
    required this.categoryId,
  });

  final int id;
  final String name;
  final String? sku;
  final double price;
  final double cost;
  final double weight;
  final String productType;
  final int? categoryId;

  factory Product.fromJson(Map<String, dynamic> json) {
    return Product(
      id: json['id'] as int,
      name: json['name'] as String? ?? '',
      sku: json['sku'] as String?,
      price: _num(json['price']),
      cost: _num(json['cost']),
      weight: _num(json['weight']),
      productType: json['product_type'] as String? ?? 'product',
      categoryId: json['category'] as int?,
    );
  }
}

double _num(Object? v) {
  if (v == null) return 0;
  if (v is num) return v.toDouble();
  return double.tryParse(v.toString()) ?? 0;
}
