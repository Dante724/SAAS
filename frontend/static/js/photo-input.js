/* ════════════════════════════════════════════════════
   PhotoInput — Build 144aq. One shared picker used everywhere a photo is added.
   Offers "Take photo" (live camera via getUserMedia) or "Choose file / gallery".
   Downscales in-browser and returns a JPEG data URL via opts.onPhoto(dataUrl).
════════════════════════════════════════════════════ */
const PhotoInput = {
  _stream: null, _opts: null,

  // opts: { onPhoto(dataUrl), max=900, title }
  capture(opts = {}) {
    this._opts = { max: opts.max || 900, onPhoto: opts.onPhoto, title: opts.title || 'Add photo' };
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${this._opts.title}</span>
        <button class="modal-close" onclick="PhotoInput._cancel()">×</button></div>
      <div class="modal-body">
        <div class="pi-choices">
          <button type="button" class="pi-choice" onclick="PhotoInput.takePhoto()">
            <span class="pi-ic">${icon('camera', 26)}</span><span class="pi-t">Take photo</span>
            <span class="pi-s">Use your camera</span></button>
          <button type="button" class="pi-choice" onclick="PhotoInput.chooseFile()">
            <span class="pi-ic">${icon('paperclip', 24)}</span><span class="pi-t">Choose file</span>
            <span class="pi-s">From files or gallery</span></button>
        </div>
      </div>`, 'modal-narrow', true);  // stack over the caller's form so it isn't destroyed
  },

  chooseFile() {
    const inp = document.createElement('input');
    inp.type = 'file'; inp.accept = 'image/*';
    inp.onchange = async () => {
      const f = inp.files && inp.files[0]; if (!f) return;
      try { const url = await this._fileToDataUrl(f); this._emit(url); }
      catch (e) { Toast.error('Could not read that image'); }
    };
    inp.click();
  },

  async takePhoto() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      Toast.error('Camera not available here — choose a file instead');
      return this.chooseFile();
    }
    try {
      this._stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
    } catch (e) {
      Toast.error('Camera blocked or unavailable — choose a file instead');
      return this.chooseFile();
    }
    Modal.close();  // pop the chooser, open the camera view (still stacked over the form)
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Take photo</span>
        <button class="modal-close" onclick="PhotoInput._cancel()">×</button></div>
      <div class="modal-body">
        <div class="pi-cam"><video id="pi-video" autoplay playsinline muted></video></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="PhotoInput._cancel()">Cancel</button>
        <button class="btn btn-primary" onclick="PhotoInput.snap()">${icon('camera', 14)} Capture</button>
      </div>`, 'modal-narrow', true);
    const v = document.getElementById('pi-video');
    if (v) { v.srcObject = this._stream; v.play?.().catch(() => {}); }
  },

  snap() {
    const v = document.getElementById('pi-video');
    if (!v || !v.videoWidth) return Toast.error('Camera still starting — try again');
    this._emit(this._scale(v, v.videoWidth, v.videoHeight));
  },

  _fileToDataUrl(file) {
    return new Promise((res, rej) => {
      const r = new FileReader();
      r.onload = () => { const img = new Image(); img.onload = () => res(this._scale(img, img.width, img.height)); img.onerror = rej; img.src = r.result; };
      r.onerror = rej; r.readAsDataURL(file);
    });
  },
  _scale(src, w, h) {
    const max = this._opts.max;
    if (w > max || h > max) { const s = Math.min(max / w, max / h); w = Math.round(w * s); h = Math.round(h * s); }
    const c = document.createElement('canvas'); c.width = w; c.height = h;
    c.getContext('2d').drawImage(src, 0, 0, w, h);
    return c.toDataURL('image/jpeg', 0.85);
  },
  _stop() { if (this._stream) { this._stream.getTracks().forEach(t => t.stop()); this._stream = null; } },
  _cancel() { this._stop(); Modal.close(); },
  _emit(url) { this._stop(); Modal.close(); if (this._opts && this._opts.onPhoto) this._opts.onPhoto(url); },
};
