/* ════════════════════════════════════════════════════════════════
   Build 144ci — internationalisation (i18n) for the whole app.

   Approach: the app renders English template literals across ~40 page
   files. Rather than rewrite every string into t('key') calls (huge and
   error-prone), we translate at the DOM level: after each render, and for
   any dynamically-added node (modals, toasts, drawers) via a MutationObserver,
   we swap whole text nodes and key attributes (placeholder/title/aria-label)
   from a phrase dictionary. English is the source; any missed phrase is a
   one-line dictionary addition.

   Built RTL-ready: switching to a right-to-left language sets
   <html dir="rtl">, which the [dir=rtl] CSS block mirrors. Spanish (ltr)
   is the first shipped catalogue; Arabic can be added later as data only.
════════════════════════════════════════════════════════════════ */
window.I18n = {
  LANGS: {
    en: { name: 'English', dir: 'ltr', locale: 'en-US' },
    es: { name: 'Español', dir: 'ltr', locale: 'es-ES' },
    // Arabic scaffold (dir rtl) — enable by populating DICT.ar later.
    ar: { name: 'العربية', dir: 'rtl', locale: 'ar' },
  },

  lang: (typeof localStorage !== 'undefined' && localStorage.getItem('ros_lang')) || 'en',
  _applied: 0,

  DICT: {
    es: {
      // ── Navigation / sections ──
      "My company": "Mi empresa", "Overview": "Resumen", "Tasks": "Tareas",
      "Inquiries": "Consultas", "Chats": "Chats", "Bookings": "Reservas",
      "Work orders": "Órdenes de trabajo", "Contacts": "Contactos", "Operations": "Operaciones",
      "Sales": "Ventas", "Inventory": "Inventario", "Finance": "Finanzas", "Cash": "Caja",
      "Insights": "Análisis", "Reports": "Informes", "Settings": "Configuración",
      "Dashboard": "Panel", "Employee hours": "Horas de empleados", "Services": "Servicios",
      "Products": "Productos", "Bundles": "Paquetes", "Suppliers": "Proveedores",
      "Warehouses": "Almacenes", "Purchase orders": "Órdenes de compra", "Employees": "Empleados",
      "Roles": "Roles", "Locations": "Ubicaciones", "Documents": "Documentos",
      "Automations": "Automatizaciones", "Payroll": "Nómina", "Loyalty": "Fidelidad",
      "Company": "Empresa", "General": "General", "Public pages": "Páginas públicas",
      "Notifications": "Notificaciones", "Integrations": "Integraciones", "Timesheets": "Registros de horas",
      // ── Common actions ──
      "Sign in": "Iniciar sesión", "Sign in →": "Iniciar sesión →", "Sign out": "Cerrar sesión",
      "Signed out": "Sesión cerrada", "Signing in…": "Iniciando sesión…", "Create an account": "Crear una cuenta",
      "Forgot password?": "¿Olvidaste tu contraseña?", "Save": "Guardar", "Saved": "Guardado",
      "Cancel": "Cancelar", "Close": "Cerrar", "Delete": "Eliminar", "Remove": "Quitar",
      "Edit": "Editar", "Add": "Añadir", "Create": "Crear", "Update": "Actualizar",
      "Open": "Abrir", "Open →": "Abrir →", "Done": "Hecho", "Back": "Atrás",
      "Search": "Buscar", "Search…": "Buscar…", "Filter": "Filtrar", "Filters": "Filtros",
      "Export": "Exportar", "Import": "Importar", "Print": "Imprimir", "Send": "Enviar",
      "Apply": "Aplicar", "Reset": "Restablecer", "Continue": "Continuar", "Next": "Siguiente",
      "Previous": "Anterior", "Confirm": "Confirmar", "Yes": "Sí", "No": "No",
      "Actions": "Acciones", "View": "Ver", "Details": "Detalles", "More": "Más",
      "Select": "Seleccionar", "All": "Todos", "None": "Ninguno", "Clear": "Limpiar",
      "Refresh": "Actualizar", "Duplicate": "Duplicar", "Download": "Descargar", "Upload": "Subir",
      "Copy": "Copiar", "Copied": "Copiado", "Manage": "Gestionar", "Assign": "Asignar",
      // ── Auth flows ──
      "Reset your password": "Restablece tu contraseña", "Send reset link": "Enviar enlace de restablecimiento",
      "Choose a new password": "Elige una nueva contraseña", "New password": "Nueva contraseña",
      "Confirm password": "Confirmar contraseña", "Set new password": "Establecer nueva contraseña",
      "← Back to sign in": "← Volver a iniciar sesión", "Email": "Correo electrónico",
      "Work email": "Correo del trabajo", "Password": "Contraseña", "Your name": "Tu nombre",
      "Company name": "Nombre de la empresa", "Create your shop": "Crea tu taller",
      "Business type": "Tipo de negocio", "Region": "Región", "Currency": "Moneda",
      "Team size": "Tamaño del equipo", "Phone (optional)": "Teléfono (opcional)",
      "Create shop & sign in →": "Crear taller e iniciar sesión →", "Join your team": "Únete a tu equipo",
      "Accept & sign in →": "Aceptar e iniciar sesión →", "Set up two-factor authentication": "Configurar autenticación de dos factores",
      "Verify & turn on": "Verificar y activar", "Invite a teammate": "Invitar a un compañero",
      "Invite teammate": "Invitar compañero", "Send invitation": "Enviar invitación",
      "Set password": "Establecer contraseña", "Reset 2FA": "Restablecer 2FA",
      "Pending invitations": "Invitaciones pendientes", "Resend": "Reenviar",
      "Security": "Seguridad", "Require 2FA for everyone": "Requerir 2FA para todos",
      // ── Statuses ──
      "Active": "Activo", "Inactive": "Inactivo", "Pending": "Pendiente", "Completed": "Completado",
      "Cancelled": "Cancelado", "Draft": "Borrador", "Paid": "Pagado", "Unpaid": "No pagado",
      "Overdue": "Vencido", "Open": "Abierto", "Closed": "Cerrado", "In progress": "En progreso",
      "New": "Nuevo", "Approved": "Aprobado", "Rejected": "Rechazado", "Trial": "Prueba",
      "Suspended": "Suspendido", "Expired": "Expirado", "Invited": "Invitado",
      // ── Entities / labels ──
      "Client": "Cliente", "Clients": "Clientes", "Customer": "Cliente", "Customers": "Clientes",
      "Order": "Orden", "Orders": "Órdenes", "Invoice": "Factura", "Invoices": "Facturas",
      "Payment": "Pago", "Payments": "Pagos", "Product": "Producto", "Service": "Servicio",
      "Supplier": "Proveedor", "Employee": "Empleado", "Device": "Dispositivo", "Asset": "Activo",
      "Assets": "Activos", "Category": "Categoría", "Categories": "Categorías", "Name": "Nombre",
      "Phone": "Teléfono", "Address": "Dirección", "Notes": "Notas", "Note": "Nota",
      "Status": "Estado", "Date": "Fecha", "Amount": "Importe", "Total": "Total",
      "Subtotal": "Subtotal", "Discount": "Descuento", "Tax": "Impuesto", "Quantity": "Cantidad",
      "Price": "Precio", "Cost": "Costo", "Balance": "Saldo", "Role": "Rol", "Type": "Tipo",
      "Description": "Descripción", "Title": "Título", "Location": "Ubicación", "Warehouse": "Almacén",
      "Barcode": "Código de barras", "SKU": "SKU", "Serial": "Número de serie", "Brand": "Marca",
      "Model": "Modelo", "Warranty": "Garantía", "Due date": "Fecha de vencimiento",
      "Created": "Creado", "Updated": "Actualizado", "Technician": "Técnico", "Manager": "Gerente",
      "Owner": "Propietario", "Admin": "Administrador", "Staff": "Personal", "Team": "Equipo",
      "Company name": "Nombre de la empresa", "Website": "Sitio web", "Country": "País",
      "Timezone": "Zona horaria", "Language": "Idioma", "Comment": "Comentario", "Comments": "Comentarios",
      "Attachments": "Adjuntos", "History": "Historial", "Activity": "Actividad", "Summary": "Resumen",
      "Loading…": "Cargando…", "No results": "Sin resultados", "No data": "Sin datos",
      "Welcome back": "Bienvenido de nuevo", "Columns": "Columnas", "Saved views": "Vistas guardadas",
      "This week": "Esta semana", "This month": "Este mes", "Today": "Hoy", "Yesterday": "Ayer",
      "Revenue": "Ingresos", "Profit": "Beneficio", "Expenses": "Gastos", "Balance due": "Saldo pendiente",
      "My payroll": "Mi nómina", "Your profile": "Tu perfil", "Profile": "Perfil",
      // ── Build 144ck: full-coverage sweep (walked every page) ──
      // Field labels & table headers
      "Qty": "Cant.", "Value": "Valor", "Received": "Recibido", "Priority": "Prioridad",
      "Condition": "Condición", "Color": "Color", "Colour": "Color", "Channel": "Canal",
      "Note (optional)": "Nota (opcional)", "Optional note": "Nota opcional", "Message": "Mensaje",
      "Work order": "Orden de trabajo", "Work order type": "Tipo de orden de trabajo",
      "Work order id": "ID de orden de trabajo", "Work order (optional)": "Orden de trabajo (opcional)",
      "To": "Para", "From": "De", "Time": "Hora", "Start": "Inicio", "Reserved": "Reservado",
      "Reason": "Motivo", "Reason / reference": "Motivo / referencia", "Group": "Grupo",
      "Action": "Acción", "Unit": "Unidad", "Unit cost": "Costo unitario", "Template": "Plantilla",
      "Subject": "Asunto", "Sale price": "Precio de venta", "Penalties": "Sanciones",
      "Bonuses": "Bonificaciones", "Ordered": "Pedido", "Margin": "Margen", "Kind": "Tipo",
      "Initial status": "Estado inicial", "Expected delivery": "Entrega prevista",
      "Default duration (minutes)": "Duración predeterminada (minutos)",
      "Default asset type": "Tipo de activo predeterminado", "Copies of each": "Copias de cada",
      "Commission": "Comisión", "Base": "Base", "Available": "Disponible", "Zone": "Zona",
      "Warranty (days)": "Garantía (días)", "Weight (kg)": "Peso (kg)", "Webhook URL": "URL de webhook",
      "Supplier name": "Nombre del proveedor", "Product name": "Nombre del producto",
      "Warehouse / location": "Almacén / ubicación", "With employee": "Con empleado",
      "Why (shown to customer)": "Por qué (mostrado al cliente)", "When status becomes": "Cuando el estado sea",
      "When (event)": "Cuándo (evento)", "Website status widget": "Widget de estado del sitio web",
      "Room": "Sala", "Bay / post": "Bahía / puesto", "Equipment": "Equipo", "Zone": "Zona",
      "Label": "Etiqueta", "Letter": "Carta", "Value list": "Lista de valores",
      "Text area": "Área de texto", "Text": "Texto", "By price type": "Por tipo de precio",
      "Fixed %": "% fijo", "Cash": "Efectivo", "Cashless": "Sin efectivo", "To do": "Por hacer",
      "Vehicle labels": "Etiquetas de vehículos", "Tracking": "Seguimiento", "Reply": "Responder",
      "Approve": "Aprobar", "Reject": "Rechazar", "Void": "Anular", "Receive": "Recibir",
      // Buttons (create actions)
      "+ Add": "+ Añadir", "+ New": "+ Nuevo", "+ New rule": "+ Nueva regla",
      "+ New document": "+ Nuevo documento", "+ New purchase order": "+ Nueva orden de compra",
      "+ Invoice": "+ Factura", "+ Estimate": "+ Presupuesto", "+ Add resource": "+ Añadir recurso",
      "+ Add field": "+ Añadir campo", "+ Add asset": "+ Añadir activo", "+ Product": "+ Producto",
      "+ Service": "+ Servicio", "+ Contact": "+ Contacto", "Add product": "Añadir producto",
      "Add asset": "Añadir activo", "New asset": "Nuevo activo", "Add at least one product": "Añade al menos un producto",
      // Section / tab names
      "Estimate": "Presupuesto", "Estimates": "Presupuestos", "Purchases": "Compras",
      "Communication": "Comunicación", "Recommendations": "Recomendaciones",
      "Invoices & payments": "Facturas y pagos", "Services & products": "Servicios y productos",
      "Services and products": "Servicios y productos", "Work orders and estimates": "Órdenes y presupuestos",
      "Public pages": "Páginas públicas", "Adjustments": "Ajustes", "Adjustment": "Ajuste",
      "Transfers": "Transferencias", "Transfer": "Transferencia", "Returns": "Devoluciones",
      "Conversions": "Conversiones", "Stocktake": "Inventario físico", "Write-off": "Baja",
      "Gift cards": "Tarjetas regalo", "Coupons": "Cupones", "Points": "Puntos",
      // Toasts / messages
      "Name is required": "El nombre es obligatorio", "Name required": "Nombre obligatorio",
      "Title is required": "El título es obligatorio", "Enter an amount": "Introduce un importe",
      "Enter a positive amount": "Introduce un importe positivo", "Enter a positive quantity": "Introduce una cantidad positiva",
      "Enter a supplier name": "Introduce el nombre del proveedor", "Enter an identifier first": "Introduce primero un identificador",
      "Deleted": "Eliminado", "Removed": "Eliminado", "Rescheduled": "Reprogramado", "Voided": "Anulado",
      "Select a client": "Selecciona un cliente", "Select at least one line": "Selecciona al menos una línea",
      "Pick a work order": "Elige una orden de trabajo", "Pick a warehouse": "Elige un almacén",
      "Paste some CSV first": "Pega primero algún CSV", "No discount": "Sin descuento",
      "Field added": "Campo añadido", "Converted to product": "Convertido a producto",
      "Balance updated": "Saldo actualizado", "Status updated": "Estado actualizado",
      "Supplier added": "Proveedor añadido", "Supplier deleted": "Proveedor eliminado",
      "Snippet copied": "Fragmento copiado", "Serial generated": "Número de serie generado",
      "Tracking link copied": "Enlace de seguimiento copiado", "Type a reply first": "Escribe primero una respuesta",
      "Type a message": "Escribe un mensaje", "Timer started": "Temporizador iniciado",
      "Timer stopped": "Temporizador detenido", "Timer started on this task": "Temporizador iniciado en esta tarea",
      "Timer started on this order": "Temporizador iniciado en esta orden",
      "Template created": "Plantilla creada", "Template saved": "Plantilla guardada", "Template updated": "Plantilla actualizada",
      "Tax added": "Impuesto añadido", "Tax created": "Impuesto creado", "Tax removed": "Impuesto eliminado",
      "Tax stack saved": "Grupo de impuestos guardado", "Task deleted": "Tarea eliminada",
      "Type created": "Tipo creado", "Type updated": "Tipo actualizado", "Type deleted": "Tipo eliminado",
      "Transfer drafted": "Transferencia en borrador", "Transfer complete": "Transferencia completada",
      "Transfer canceled": "Transferencia cancelada", "Warehouse restored": "Almacén restaurado",
      "Stock adjusted": "Existencias ajustadas", "Stock moved": "Existencias movidas", "Stock written off": "Existencias dadas de baja",
      "Tick one or more products first": "Marca uno o más productos primero",
      "You need at least two bins to transfer": "Necesitas al menos dos ubicaciones para transferir",
      // Weekdays (abbrev.)
      "Mon": "Lun", "Tue": "Mar", "Wed": "Mié", "Thu": "Jue", "Fri": "Vie", "Sat": "Sáb", "Sun": "Dom",
      "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles", "Thursday": "Jueves",
      "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo",
    },
  },

  // Suffixes we strip before lookup, then re-append (keeps arrows/colons on buttons).
  _SUFFIX: [' →', '→', ':', ' *', '…'],

  _lookup(raw) {
    const d = this.DICT[this.lang];
    if (!d) return null;
    const t = raw.trim();
    if (!t) return null;
    if (d[t] != null) return raw.replace(t, d[t]);
    // try trimming a trailing suffix (e.g. "Sign in →", "Name:")
    for (const s of this._SUFFIX) {
      if (t.endsWith(s)) {
        const core = t.slice(0, -s.length).trim();
        if (d[core] != null) return raw.replace(t, d[core] + s);
      }
    }
    return null;
  },

  _ATTRS: ['placeholder', 'title', 'aria-label', 'alt'],

  translateNode(root) {
    if (this.lang === 'en' || !root) return;
    const d = this.DICT[this.lang]; if (!d) return;
    // 1) text nodes
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue && n.nodeValue.trim() && !n._i18n) ?
        NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
    const nodes = [];
    let n; while ((n = walker.nextNode())) nodes.push(n);
    nodes.forEach(node => {
      const rep = this._lookup(node.nodeValue);
      if (rep != null) node.nodeValue = rep;
      node._i18n = true;   // don't re-scan translated nodes
    });
    // 2) attributes on elements
    const els = root.querySelectorAll ? root.querySelectorAll('[placeholder],[title],[aria-label],[alt]') : [];
    els.forEach(el => {
      this._ATTRS.forEach(a => {
        const v = el.getAttribute(a);
        if (v && !el['_i18n_' + a]) {
          const rep = this._lookup(v);
          if (rep != null) el.setAttribute(a, rep);
          el['_i18n_' + a] = true;
        }
      });
    });
  },

  apply(root) {
    if (this.lang === 'en') return;
    this._applied++;
    this.translateNode(root || document.body);
  },

  /** Text translation for code that wants an explicit lookup (e.g. Toasts built as strings). */
  t(en) {
    if (this.lang === 'en') return en;
    const rep = this._lookup(en);
    return rep != null ? rep : en;
  },

  setLang(l) {
    if (!this.LANGS[l] || l === this.lang) return;
    try { localStorage.setItem('ros_lang', l); } catch (e) {}
    // Persist to the server so the choice follows the user across devices (best-effort),
    // then reload. A DOM-swap translator can't reliably *un*-translate an already-swapped
    // page, so a clean reload guarantees correct state in either direction (incl. LTR↔RTL).
    const go = () => { try { location.reload(); } catch (e) { this.lang = l; this.boot(); } };
    try {
      if (typeof put === 'function' && typeof Auth !== 'undefined' && Auth.token) {
        Promise.resolve(put('/api/prefs/lang', { value: { lang: l } })).finally(go);
        setTimeout(go, 400);   // don't wait forever on the network
      } else { go(); }
    } catch (e) { go(); }
  },

  /** Compact language selector for the topbar. */
  switcherHtml() {
    const opts = Object.keys(this.LANGS)
      .filter(k => k === 'en' || (this.DICT[k]))   // only offer languages that have a catalogue
      .map(k => `<option value="${k}" ${this.lang === k ? 'selected' : ''}>${this.LANGS[k].name}</option>`).join('');
    return `<select class="lang-switcher" onchange="I18n.setLang(this.value)" title="Language">${opts}</select>`;
  },

  _observing: false,
  observe() {
    if (this._observing || typeof MutationObserver === 'undefined') return;
    this._observing = true;
    let queued = null;
    const obs = new MutationObserver(muts => {
      if (this.lang === 'en') return;
      for (const m of muts) {
        m.addedNodes.forEach(node => {
          if (node.nodeType === 1) { clearTimeout(queued); queued = setTimeout(() => this.translateNode(node), 0); }
        });
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  },

  boot() {
    document.documentElement.lang = this.lang;
    document.documentElement.dir = this.LANGS[this.lang]?.dir || 'ltr';
    if (typeof fmt !== 'undefined') fmt._locale = this.LANGS[this.lang]?.locale || 'en-US';
    this.observe();
    this.apply(document.body);
  },
};
