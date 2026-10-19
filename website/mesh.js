/* Animated gradient mesh (WebGL). Noise-warped colour bands drifting slowly —
   cream → sherbet → lavender → indigo → ruby, per the design system.
   Pauses when off-screen; falls back to the static CSS mesh if WebGL is unavailable. */
(() => {
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const COLORS = ["#f5e9d4", "#ffb38a", "#f96bee", "#a960ee", "#533afd", "#ea2261", "#ffcb57"];
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255);

  const FRAG = `
precision highp float;
uniform vec2 r; uniform float t; uniform vec3 c[7]; uniform float seed;
vec3 mod289(vec3 x){return x-floor(x*(1./289.))*289.;}
vec4 mod289(vec4 x){return x-floor(x*(1./289.))*289.;}
vec4 perm(vec4 x){return mod289(((x*34.)+1.)*x);}
float snoise(vec3 v){
  const vec2 C=vec2(1./6.,1./3.); const vec4 D=vec4(0.,.5,1.,2.);
  vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
  vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
  vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy; i=mod289(i);
  vec4 p=perm(perm(perm(i.z+vec4(0.,i1.z,i2.z,1.))+i.y+vec4(0.,i1.y,i2.y,1.))+i.x+vec4(0.,i1.x,i2.x,1.));
  float n_=.142857142857; vec3 ns=n_*D.wyz-D.xzx; vec4 j=p-49.*floor(p*ns.z*ns.z);
  vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.*x_); vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.-abs(x)-abs(y);
  vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw); vec4 s0=floor(b0)*2.+1.; vec4 s1=floor(b1)*2.+1.; vec4 sh=-step(h,vec4(0.));
  vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
  vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
  vec4 nm=1.79284291400159-.85373472095314*vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3));
  p0*=nm.x; p1*=nm.y; p2*=nm.z; p3*=nm.w;
  vec4 m=max(.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.); m=m*m;
  return 42.*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
}
vec3 pal(float x){
  x=clamp(x,0.,1.)*6.;
  vec3 a=c[0];
  a=mix(a,c[1],smoothstep(0.,1.,x));
  a=mix(a,c[2],smoothstep(1.,2.,x));
  a=mix(a,c[3],smoothstep(2.,3.,x));
  a=mix(a,c[4],smoothstep(3.,4.,x));
  a=mix(a,c[5],smoothstep(4.,5.,x));
  a=mix(a,c[6],smoothstep(5.,6.,x));
  return a;
}
void main(){
  vec2 uv=gl_FragCoord.xy/r; float asp=r.x/r.y; vec2 p=vec2(uv.x*asp,uv.y);
  float T=t*.035+seed;
  float w1=snoise(vec3(p*vec2(.55,1.1),T));
  float w2=snoise(vec3(p*vec2(1.1,1.9)+4.,T*1.3));
  float x=uv.x*.95+.22*w1+.08*w2+.08*sin(uv.y*3.+T*2.);
  vec3 col=pal(x);
  float glow=snoise(vec3(p*1.6+9.,T*.8));
  col=mix(col,vec3(1.),.10*smoothstep(.2,.9,glow));
  col+= (fract(sin(dot(gl_FragCoord.xy,vec2(12.9898,78.233)))*43758.5453)-.5)/255.*3.;
  gl_FragColor=vec4(col,1.);
}`;
  const VERT = "attribute vec2 a;void main(){gl_Position=vec4(a,0.,1.);}";

  function start(canvas, seed) {
    if (!canvas) return;
    const gl = canvas.getContext("webgl", { antialias: false, premultipliedAlpha: false, alpha: false });
    if (!gl) return;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return gl.getShaderParameter(s, gl.COMPILE_STATUS) ? s : null; };
    const vs = sh(gl.VERTEX_SHADER, VERT), fs = sh(gl.FRAGMENT_SHADER, FRAG);
    if (!vs || !fs) return;
    const pr = gl.createProgram(); gl.attachShader(pr, vs); gl.attachShader(pr, fs); gl.linkProgram(pr);
    if (!gl.getProgramParameter(pr, gl.LINK_STATUS)) return;
    gl.useProgram(pr);
    const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(pr, "a"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    const uR = gl.getUniformLocation(pr, "r"), uT = gl.getUniformLocation(pr, "t");
    gl.uniform3fv(gl.getUniformLocation(pr, "c"), new Float32Array(COLORS.flatMap(hex)));
    gl.uniform1f(gl.getUniformLocation(pr, "seed"), seed);
    const DPR = Math.min(devicePixelRatio || 1, 1.5) * 0.5; // the mesh is soft; render at half-res
    const size = () => {
      const w = canvas.clientWidth, h = canvas.clientHeight;
      canvas.width = Math.max(1, w * DPR); canvas.height = Math.max(1, h * DPR);
      gl.viewport(0, 0, canvas.width, canvas.height); gl.uniform2f(uR, canvas.width, canvas.height);
    };
    size(); addEventListener("resize", size);
    let visible = true, t0 = performance.now(), raf;
    let last = 0;
    const frame = now => { if (now - last >= 33) { last = now; gl.uniform1f(uT, (now - t0) / 1000); gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4); } if (visible && !reduced && !document.hidden) raf = requestAnimationFrame(frame); };
    document.addEventListener("visibilitychange", () => { cancelAnimationFrame(raf); if (visible && !document.hidden) raf = requestAnimationFrame(frame); });
    new IntersectionObserver(([e]) => { visible = e.isIntersecting; cancelAnimationFrame(raf); if (visible) raf = requestAnimationFrame(frame); }).observe(canvas);
    raf = requestAnimationFrame(frame);
    canvas.style.opacity = "1";
    canvas.previousElementSibling && (canvas.previousElementSibling.style.opacity = "0");
  }
  start(document.getElementById("mesh"), 0);
  start(document.getElementById("mesh2"), 7.3);
})();
