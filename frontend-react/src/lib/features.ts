// Features that exist in the code but aren't trustworthy yet, so they stay out of the app until
// they are. Xero's push reports success without sending anything, and the Shopify/WooCommerce
// connection stores the settings but never syncs. Turn them on for a build with
// VITE_BETA_INTEGRATIONS=1 once they really work.
const beta = import.meta.env.VITE_BETA_INTEGRATIONS === "1";

export const FEATURES = {
  xero: beta,
  ecommerce: beta,
} as const;
