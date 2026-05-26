import '_parse.dart';

class SaleItemSummary {
  SaleItemSummary({
    required this.productName,
    required this.quantity,
    required this.priceAtSale,
  });

  final String productName;
  final int quantity;
  final double priceAtSale;

  factory SaleItemSummary.fromJson(Map<String, dynamic> json) {
    return SaleItemSummary(
      productName: json['product_name'] as String? ?? '—',
      quantity: asInt(json['quantity']),
      priceAtSale: asDouble(json['price_at_sale']),
    );
  }
}

class Sale {
  Sale({
    required this.id,
    required this.invoiceNumber,
    required this.status,
    required this.totalAmount,
    required this.customerName,
    required this.createdAt,
    required this.paymentStatus,
    required this.items,
  });

  final int id;
  final String invoiceNumber;
  final String status;
  final double totalAmount;
  final String customerName;
  final DateTime? createdAt;
  final String paymentStatus;
  final List<SaleItemSummary> items;

  factory Sale.fromJson(Map<String, dynamic> json) {
    final itemsJson = json['items_response'];
    final items = itemsJson is List
        ? itemsJson
            .map((e) => SaleItemSummary.fromJson(Map<String, dynamic>.from(e)))
            .toList()
        : <SaleItemSummary>[];
    return Sale(
      id: json['id'] as int,
      invoiceNumber: json['invoice_number'] as String? ?? '',
      status: json['status'] as String? ?? 'pending',
      totalAmount: asDouble(json['total_amount']),
      customerName:
          json['customer_name'] as String? ?? 'Walk-in customer',
      createdAt: json['created_at'] == null
          ? null
          : DateTime.tryParse(json['created_at'] as String),
      paymentStatus: json['payment_status'] as String? ?? 'Credit',
      items: items,
    );
  }
}
