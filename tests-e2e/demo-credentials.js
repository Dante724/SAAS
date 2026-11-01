// Demo-shop passwords for e2e runs (launch QA F10): the env wins, else the `.env.demo` file the
// demo seed writes at the repo root (scripts/demo_credentials.py). No password lives in the repo.
const fs = require('fs');
const path = require('path');

function fromFile(key) {
  try {
    const txt = fs.readFileSync(path.join(__dirname, '..', '.env.demo'), 'utf8');
    const m = txt.match(new RegExp(`^${key}=(.*)$`, 'm'));
    return m ? m[1].trim() : '';
  } catch {
    return '';
  }
}

const demoAdminPassword = () => process.env.DEMO_ADMIN_PASSWORD || fromFile('DEMO_ADMIN_PASSWORD');
const demoStaffPassword = () => process.env.DEMO_STAFF_PASSWORD || fromFile('DEMO_STAFF_PASSWORD');

module.exports = { demoAdminPassword, demoStaffPassword };
