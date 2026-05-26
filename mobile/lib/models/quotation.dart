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
      totalAmount: (json['total_amount'] as num?)?.toDouble() ??
          double.tryParse(json['total_amount']?.toString() ?? '') ??
          0,
      status: json['status'] as String? ?? 'draft',
      createdAt: json['created_at'] == null
          ? null
          : DateTime.tryParse(json['created_at'] as String),
    );
  }
}
