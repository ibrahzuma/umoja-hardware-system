import '_parse.dart';

class Quotation {
  Quotation({
    required this.id,
    required this.customerName,
    required this.totalAmount,
    required this.status,
    required this.createdAt,
  });

  final int id;
  final String customerName;
  final double totalAmount;
  final String status;
  final DateTime? createdAt;

  factory Quotation.fromJson(Map<String, dynamic> json) {
    return Quotation(
      id: json['id'] as int,
      customerName: json['customer_name'] as String? ?? 'Walk-in customer',
      totalAmount: asDouble(json['total_amount']),
      status: json['status'] as String? ?? 'draft',
      createdAt: json['created_at'] == null
          ? null
          : DateTime.tryParse(json['created_at'] as String),
    );
  }
}
