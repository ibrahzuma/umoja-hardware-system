class Stock {
  Stock({
    required this.id,
    required this.productId,
    required this.branchId,
    required this.quantity,
    required this.lowStockThreshold,
  });

  final int id;
  final int productId;
  final int branchId;
  final int quantity;
  final int lowStockThreshold;

  bool get isLow => quantity <= lowStockThreshold;

  factory Stock.fromJson(Map<String, dynamic> json) {
    return Stock(
      id: json['id'] as int,
      productId: json['product'] as int,
      branchId: json['branch'] as int,
      quantity: (json['quantity'] as num?)?.toInt() ?? 0,
      lowStockThreshold:
          (json['low_stock_threshold'] as num?)?.toInt() ?? 0,
    );
  }
}
