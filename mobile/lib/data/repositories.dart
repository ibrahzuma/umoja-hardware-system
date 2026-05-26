import 'package:flutter_riverpod/flutter_riverpod.dart';

import '../models/branch.dart';
import '../models/customer.dart';
import '../models/product.dart';
import '../models/quotation.dart';
import '../models/sale.dart';
import '../models/stock.dart';
import 'api_client.dart';

class BranchRepository {
  BranchRepository(this._api);
  final ApiClient _api;

  Future<List<Branch>> list() async {
    final results = await _api.list('/api/branches/');
    return results
        .map((j) => Branch.fromJson(Map<String, dynamic>.from(j)))
        .toList();
  }
}

class ProductRepository {
  ProductRepository(this._api);
  final ApiClient _api;

  Future<List<Product>> list({String? search}) async {
    final results = await _api.list(
      '/api/products/',
      query: {if (search != null && search.isNotEmpty) 'search': search},
    );
    return results
        .map((j) => Product.fromJson(Map<String, dynamic>.from(j)))
        .toList();
  }
}

class StockRepository {
  StockRepository(this._api);
  final ApiClient _api;

  Future<List<Stock>> list({int? branch}) async {
    final results = await _api.list(
      '/api/stocks/',
      query: {if (branch != null) 'branch': branch},
    );
    return results
        .map((j) => Stock.fromJson(Map<String, dynamic>.from(j)))
        .toList();
  }
}

class CustomerRepository {
  CustomerRepository(this._api);
  final ApiClient _api;

  Future<List<Customer>> list({String? search}) async {
    final results = await _api.list(
      '/api/customers/',
      query: {if (search != null && search.isNotEmpty) 'search': search},
    );
    return results
        .map((j) => Customer.fromJson(Map<String, dynamic>.from(j)))
        .toList();
  }

  Future<Customer> create({
    required String name,
    String phone = '',
    String email = '',
    String address = '',
  }) async {
    final data = await _api.post('/api/customers/', {
      'name': name,
      'phone': phone,
      'email': email,
      'address': address,
    });
    return Customer.fromJson(data);
  }
}

class SaleRepository {
  SaleRepository(this._api);
  final ApiClient _api;

  Future<List<Sale>> list({int? customer}) async {
    final results = await _api.list(
      '/api/sales/',
      query: {if (customer != null) 'customer': customer},
    );
    return results
        .map((j) => Sale.fromJson(Map<String, dynamic>.from(j)))
        .toList();
  }

  /// Create a sale. `items` is a list of {product, quantity, price_at_sale}.
  Future<Sale> create({
    required int branchId,
    required List<Map<String, dynamic>> items,
    String customerName = 'Walk-in Customer',
    int? customerId,
    Map<String, dynamic>? paymentDetails,
  }) async {
    final body = <String, dynamic>{
      'branch': branchId,
      'customer_name': customerName,
      if (customerId != null) 'customer': customerId,
      'items': items,
      if (paymentDetails != null) 'payment_details': paymentDetails,
    };
    final data = await _api.post('/api/sales/', body);
    return Sale.fromJson(data);
  }
}

class QuotationRepository {
  QuotationRepository(this._api);
  final ApiClient _api;

  Future<List<Quotation>> list() async {
    final results = await _api.list('/api/quotations/');
    return results
        .map((j) => Quotation.fromJson(Map<String, dynamic>.from(j)))
        .toList();
  }
}

// ---------------------------------------------------------------------------
// Providers
// ---------------------------------------------------------------------------

final branchRepoProvider =
    Provider<BranchRepository>((ref) => BranchRepository(ref.watch(apiClientProvider)));
final branchListProvider = FutureProvider<List<Branch>>((ref) {
  return ref.watch(branchRepoProvider).list();
});

final productRepoProvider =
    Provider<ProductRepository>((ref) => ProductRepository(ref.watch(apiClientProvider)));
final stockRepoProvider =
    Provider<StockRepository>((ref) => StockRepository(ref.watch(apiClientProvider)));
final customerRepoProvider = Provider<CustomerRepository>(
    (ref) => CustomerRepository(ref.watch(apiClientProvider)));
final saleRepoProvider =
    Provider<SaleRepository>((ref) => SaleRepository(ref.watch(apiClientProvider)));
final quotationRepoProvider = Provider<QuotationRepository>(
    (ref) => QuotationRepository(ref.watch(apiClientProvider)));

// Async list providers used by the UI

final productListProvider =
    FutureProvider.family<List<Product>, String>((ref, search) {
  return ref.watch(productRepoProvider).list(search: search);
});

final stockListProvider = FutureProvider<List<Stock>>((ref) {
  return ref.watch(stockRepoProvider).list();
});

final customerListProvider =
    FutureProvider.family<List<Customer>, String>((ref, search) {
  return ref.watch(customerRepoProvider).list(search: search);
});

final quotationListProvider = FutureProvider<List<Quotation>>((ref) {
  return ref.watch(quotationRepoProvider).list();
});

final customerSalesProvider =
    FutureProvider.family<List<Sale>, int>((ref, customerId) {
  return ref.watch(saleRepoProvider).list(customer: customerId);
});
