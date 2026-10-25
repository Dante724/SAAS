export type Product = {
  id: string; name?: string; code?: string; sku?: string; mpn?: string | null; barcode?: string; barcode_type?: string; extra_barcodes?: string[]; brand?: string;
  category_id?: string | null; description?: string; uom?: string;
  cost_price?: number; avg_cost?: number; sell_price?: number; min_stock?: number;
  serialized?: boolean; expiry_tracking?: boolean; weight?: number; dimensions?: string;
  image_data?: string | null; warranty_days?: number; supplier_warranty_days?: number;
  parent_id?: string | null; variant_attrs?: Record<string, string>; variant_count?: number;
  margin_pct?: number | null; qty_available?: number; qty_reserved?: number; low_stock?: boolean; active?: boolean;
};
export type Category = { id: string; name: string; count?: number; children?: Category[] };
export type Supplier = { id: string; name?: string; contact_name?: string; phone?: string; email?: string; lead_days?: number; balance?: number; currency?: string; client_id?: string | null };
export type POLine = { id: string; part_id?: string; part?: string; qty_ordered?: number; qty_received?: number; unit_cost?: number };
export type PO = { id: string; po_number?: string; status?: string; supplier_id?: string; total?: number; subtotal?: number; expected_at?: string | null; ordered_at?: string | null; lines?: POLine[] };

export type Service = { id: string; name?: string; category?: string; category_id?: string | null; category_name?: string; is_labor?: boolean; unit?: string; price?: number; cost_price?: number; duration_min?: number; flat_rate?: boolean; margin_pct?: number; description?: string;
  warranty_days?: number; barcode?: string | null; bookable?: boolean; allowed_employee_ids?: string[]; required_resource_types?: string[]; active?: boolean };
export type Bundle = { id: string; name?: string; price_mode?: string; price?: number; total_std?: number; sum_price?: number; effective_price?: number; broken?: boolean; components?: { id: string; name?: string; qty?: number; std_price?: number; ref_type?: string; ref_id?: string; missing?: boolean }[] };
export type Asset = { id: string; owner_type?: string; owner_name?: string; asset_type?: string; asset_label?: string; template_key?: string; brand?: string; model?: string; serial?: string; imei?: string; condition?: string; asset_status?: string; order_count?: number; image_data?: string | null };

// Re-export the currency-aware canonical helper so existing inventory importers keep resolving.
export { currencySymbol, money } from "../orders/types";
