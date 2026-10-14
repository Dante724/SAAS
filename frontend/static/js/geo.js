/* Build 144da — shared geography table: the single source of truth linking a
   country to its ISO currency (code + symbol), a sensible primary IANA
   timezone, and the onboarding region bucket (us|ca|eu|mena|other).

   Any "Country" picker in the app reads from here so choosing a country can
   auto-fill currency + timezone. Values are suggestions — always editable.
   Multi-timezone countries carry a common default; the timezone control stays
   open so the user can refine. */
(function () {
  // [code, name, currency_code, symbol, timezone, region]
  const ROWS = [
    ['US', 'United States', 'USD', '$', 'America/New_York', 'us'],
    ['CA', 'Canada', 'CAD', '$', 'America/Toronto', 'ca'],
    ['GB', 'United Kingdom', 'GBP', '£', 'Europe/London', 'eu'],
    ['IE', 'Ireland', 'EUR', '€', 'Europe/Dublin', 'eu'],
    ['DE', 'Germany', 'EUR', '€', 'Europe/Berlin', 'eu'],
    ['FR', 'France', 'EUR', '€', 'Europe/Paris', 'eu'],
    ['ES', 'Spain', 'EUR', '€', 'Europe/Madrid', 'eu'],
    ['IT', 'Italy', 'EUR', '€', 'Europe/Rome', 'eu'],
    ['NL', 'Netherlands', 'EUR', '€', 'Europe/Amsterdam', 'eu'],
    ['BE', 'Belgium', 'EUR', '€', 'Europe/Brussels', 'eu'],
    ['AT', 'Austria', 'EUR', '€', 'Europe/Vienna', 'eu'],
    ['PT', 'Portugal', 'EUR', '€', 'Europe/Lisbon', 'eu'],
    ['GR', 'Greece', 'EUR', '€', 'Europe/Athens', 'eu'],
    ['FI', 'Finland', 'EUR', '€', 'Europe/Helsinki', 'eu'],
    ['PL', 'Poland', 'PLN', 'zł', 'Europe/Warsaw', 'eu'],
    ['SE', 'Sweden', 'SEK', 'kr', 'Europe/Stockholm', 'eu'],
    ['NO', 'Norway', 'NOK', 'kr', 'Europe/Oslo', 'eu'],
    ['DK', 'Denmark', 'DKK', 'kr', 'Europe/Copenhagen', 'eu'],
    ['CH', 'Switzerland', 'CHF', 'CHF', 'Europe/Zurich', 'eu'],
    ['CZ', 'Czechia', 'CZK', 'Kč', 'Europe/Prague', 'eu'],
    ['RO', 'Romania', 'RON', 'lei', 'Europe/Bucharest', 'eu'],
    ['AE', 'United Arab Emirates', 'AED', 'AED', 'Asia/Dubai', 'mena'],
    ['SA', 'Saudi Arabia', 'SAR', 'SAR', 'Asia/Riyadh', 'mena'],
    ['QA', 'Qatar', 'QAR', 'QAR', 'Asia/Qatar', 'mena'],
    ['KW', 'Kuwait', 'KWD', 'KWD', 'Asia/Kuwait', 'mena'],
    ['BH', 'Bahrain', 'BHD', 'BHD', 'Asia/Bahrain', 'mena'],
    ['OM', 'Oman', 'OMR', 'OMR', 'Asia/Muscat', 'mena'],
    ['JO', 'Jordan', 'JOD', 'JOD', 'Asia/Amman', 'mena'],
    ['EG', 'Egypt', 'EGP', 'E£', 'Africa/Cairo', 'mena'],
    ['MA', 'Morocco', 'MAD', 'MAD', 'Africa/Casablanca', 'mena'],
    ['LB', 'Lebanon', 'LBP', 'LBP', 'Asia/Beirut', 'mena'],
    ['TR', 'Türkiye', 'TRY', '₺', 'Europe/Istanbul', 'mena'],
    ['IN', 'India', 'INR', '₹', 'Asia/Kolkata', 'other'],
    ['PK', 'Pakistan', 'PKR', '₨', 'Asia/Karachi', 'other'],
    ['BD', 'Bangladesh', 'BDT', '৳', 'Asia/Dhaka', 'other'],
    ['LK', 'Sri Lanka', 'LKR', 'Rs', 'Asia/Colombo', 'other'],
    ['SG', 'Singapore', 'SGD', '$', 'Asia/Singapore', 'other'],
    ['MY', 'Malaysia', 'MYR', 'RM', 'Asia/Kuala_Lumpur', 'other'],
    ['ID', 'Indonesia', 'IDR', 'Rp', 'Asia/Jakarta', 'other'],
    ['PH', 'Philippines', 'PHP', '₱', 'Asia/Manila', 'other'],
    ['TH', 'Thailand', 'THB', '฿', 'Asia/Bangkok', 'other'],
    ['VN', 'Vietnam', 'VND', '₫', 'Asia/Ho_Chi_Minh', 'other'],
    ['JP', 'Japan', 'JPY', '¥', 'Asia/Tokyo', 'other'],
    ['KR', 'South Korea', 'KRW', '₩', 'Asia/Seoul', 'other'],
    ['CN', 'China', 'CNY', '¥', 'Asia/Shanghai', 'other'],
    ['HK', 'Hong Kong', 'HKD', '$', 'Asia/Hong_Kong', 'other'],
    ['AU', 'Australia', 'AUD', '$', 'Australia/Sydney', 'other'],
    ['NZ', 'New Zealand', 'NZD', '$', 'Pacific/Auckland', 'other'],
    ['ZA', 'South Africa', 'ZAR', 'R', 'Africa/Johannesburg', 'other'],
    ['NG', 'Nigeria', 'NGN', '₦', 'Africa/Lagos', 'other'],
    ['KE', 'Kenya', 'KES', 'KSh', 'Africa/Nairobi', 'other'],
    ['MX', 'Mexico', 'MXN', '$', 'America/Mexico_City', 'other'],
    ['BR', 'Brazil', 'BRL', 'R$', 'America/Sao_Paulo', 'other'],
    ['AR', 'Argentina', 'ARS', '$', 'America/Argentina/Buenos_Aires', 'other'],
    ['CL', 'Chile', 'CLP', '$', 'America/Santiago', 'other'],
  ];

  const COUNTRIES = ROWS.map(([code, name, currency_code, symbol, timezone, region]) =>
    ({ code, name, currency_code, symbol, timezone, region }));
  const BY_CODE = {};
  COUNTRIES.forEach(c => { BY_CODE[c.code] = c; });

  window.Geo = {
    COUNTRIES,
    meta(code) { return code ? (BY_CODE[String(code).toUpperCase()] || null) : null; },
    // <option> list for a <select>; `selected` is the currently-chosen code.
    options(selected) {
      return `<option value="">— Select country —</option>` +
        COUNTRIES.map(c => `<option value="${c.code}" ${c.code === selected ? 'selected' : ''}>${c.name}</option>`).join('');
    },
  };
})();
