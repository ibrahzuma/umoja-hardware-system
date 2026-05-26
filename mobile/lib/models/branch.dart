class Branch {
  Branch({required this.id, required this.name, required this.address});

  final int id;
  final String name;
  final String address;

  factory Branch.fromJson(Map<String, dynamic> json) {
    return Branch(
      id: json['id'] as int,
      name: json['name'] as String? ?? '',
      address: json['address'] as String? ?? '',
    );
  }
}
