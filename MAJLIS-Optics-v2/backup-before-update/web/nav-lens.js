/** Visual-only refraction of our own navigation artwork. No DOM capture or remote assets. */
export class NavigationLens {
  constructor(root) {
    this.root = root;
    this.canvas = document.createElement('canvas');
    this.canvas.className = 'nav-lens';
    this.canvas.hidden = true;
    this.available = false;
    this.canvas.setAttribute('aria-hidden', 'true');
    root.append(this.canvas);
    this.textureCanvas = document.createElement('canvas');
    this.ctx = this.textureCanvas.getContext('2d');
    try {
      this.gl = this.canvas.getContext('webgl', {
        alpha: true,
        premultipliedAlpha: false,
        antialias: true,
      });
    } catch {
      this.gl = null;
    }
    if (this.gl) this.init();
    this.canvas.addEventListener('webglcontextlost', (e) => {
      e.preventDefault();
      this.available = false;
      this.canvas.hidden = true;
    });
    this.canvas.addEventListener('webglcontextrestored', () => {
      this.init();
      this.prepare();
    });
  }

  init() {
    const gl = this.gl;
    const shader = (type, code) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, code);
      gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
        throw Error('Navigation lens shader unavailable');
      return s;
    };
    try {
      this.program = gl.createProgram();
      gl.attachShader(
        this.program,
        shader(
          gl.VERTEX_SHADER,
          `attribute vec2 position;
        varying vec2 uv; void main(){uv=(position+1.0)*0.5;gl_Position=vec4(position,0.,1.);}`,
        ),
      );
      gl.attachShader(
        this.program,
        shader(
          gl.FRAGMENT_SHADER,
          `precision mediump float;
        varying vec2 uv; uniform sampler2D artwork;
        uniform vec2 size, center, radius; uniform float lift;
        uniform vec3 base;
        vec4 sampleArt(vec2 p){return texture2D(artwork,vec2(p.x/size.x,p.y/size.y));}
        void main(){
          vec2 pixel=vec2(uv.x,1.-uv.y)*size;
          vec2 d=pixel-center, q=d/radius;
          float r=pow(pow(abs(q.x),2.6)+pow(abs(q.y),2.6),1./2.6);
          float mask=1.-smoothstep(.985,1.,r);
          if(mask<=0.){gl_FragColor=vec4(0.);return;}
          // Central magnification, rapidly changing edge refraction and spectral separation.
          float bend=1.-.18*lift+.75*lift*pow(r,7.);
          vec2 source=center+d*bend;
          vec2 fringe=normalize(q+vec2(.0001))*3.2*lift*pow(r,9.);
          vec4 a=sampleArt(source), red=sampleArt(source+fringe), blue=sampleArt(source-fringe);
          vec3 tint=base*(1.+.09*(-q.y));
          vec3 content=mix(tint,a.rgb,a.a);
          content.r=mix(tint.r,red.r,red.a);
          content.b=mix(tint.b,blue.b,blue.a);
          float rim=pow(smoothstep(.94,1.,r),1.4);
          float light=.45+.35*(-q.y)+.2*(-q.x);
          content=mix(content,vec3(.87,.95,1.),rim*light*.9);
          float sheen=pow(max(0.,-q.y),8.)*lift*.10;
          content=mix(content,vec3(.9,.97,1.),sheen);
          vec3 spectrum=.5+.5*cos(vec3(0.,2.,4.)+q.y*5.+q.x*3.);
          content=mix(content,spectrum,pow(r,24.)*lift*.16);
          // Narrow reflected bands at the most curved upper/lower edges.
          float cap=smoothstep(.82,.99,abs(q.y))*pow(max(0.,1.-abs(q.x)),3.)*lift*.85;
          content=mix(content,base*.12,cap);
          gl_FragColor=vec4(content,mask);
        }`,
        ),
      );
      gl.linkProgram(this.program);
      if (!gl.getProgramParameter(this.program, gl.LINK_STATUS))
        throw Error('Navigation lens link unavailable');
      gl.useProgram(this.program);
      this.buffer = gl.createBuffer();
      gl.bindBuffer(gl.ARRAY_BUFFER, this.buffer);
      gl.bufferData(
        gl.ARRAY_BUFFER,
        new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
        gl.STATIC_DRAW,
      );
      const attr = gl.getAttribLocation(this.program, 'position');
      gl.enableVertexAttribArray(attr);
      gl.vertexAttribPointer(attr, 2, gl.FLOAT, false, 0, 0);
      this.texture = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.uniforms = Object.fromEntries(
        ['size', 'center', 'radius', 'lift', 'base'].map((n) => [
          n,
          gl.getUniformLocation(this.program, n),
        ]),
      );
      this.available = !!this.ctx;
      this.canvas.hidden = !this.available;
    } catch {
      this.available = false;
      this.canvas.hidden = true;
    }
  }

  dispose() {
    this.generation = (this.generation || 0) + 1;
    if (this.gl) {
      this.gl.deleteTexture(this.texture);
      this.gl.deleteBuffer(this.buffer);
      this.gl.deleteProgram(this.program);
    }
    this.canvas.remove();
  }

  async prepare() {
    if (!this.available) return;
    const generation = (this.generation = (this.generation || 0) + 1);
    const rect = this.root.getBoundingClientRect();
    this.pad = 40;
    this.width = rect.width + 80;
    this.height = rect.height + 80;
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.style.width = this.width + 'px';
    this.canvas.style.height = this.height + 'px';
    this.canvas.width = Math.round(this.width * ratio);
    this.canvas.height = Math.round(this.height * ratio);
    this.textureCanvas.width = this.canvas.width;
    this.textureCanvas.height = this.canvas.height;
    const ctx = this.ctx;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, this.width, this.height);
    const styles = getComputedStyle(this.root);
    this.base = styles.getPropertyValue('--glass-active-solid').trim();
    const ink = styles.getPropertyValue('--nav-ink').trim();
    const buttons = [...this.root.querySelectorAll('button[data-section]')];
    // Load once per geometry/theme change, never per animation frame.
    const images = await Promise.all(
      buttons.map(
        (button) =>
          new Promise((resolve) => {
            const svg = button.querySelector('svg').cloneNode(true);
            svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
            svg.setAttribute('width', '22');
            svg.setAttribute('height', '22');
            svg.setAttribute('stroke', ink);
            const im = new Image();
            im.onload = () => resolve(im);
            im.onerror = () => resolve(null);
            im.src =
              'data:image/svg+xml;charset=utf-8,' +
              encodeURIComponent(new XMLSerializer().serializeToString(svg));
          }),
      ),
    );
    if (generation !== this.generation) return;
    ctx.fillStyle = ink;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    buttons.forEach((button, i) => {
      const svg = button.querySelector('svg').getBoundingClientRect();
      const text = button.querySelector('span');
      const tr = text.getBoundingClientRect();
      if (images[i])
        ctx.drawImage(
          images[i],
          svg.left - rect.left + this.pad,
          svg.top - rect.top + this.pad,
          svg.width,
          svg.height,
        );
      const font = getComputedStyle(text);
      ctx.font = `${font.fontWeight} ${font.fontSize} ${font.fontFamily}`;
      ctx.fillText(
        text.textContent,
        tr.left - rect.left + tr.width / 2 + this.pad,
        tr.top - rect.top + tr.height / 2 + this.pad,
      );
    });
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      gl.RGBA,
      gl.RGBA,
      gl.UNSIGNED_BYTE,
      this.textureCanvas,
    );
    // Let the browser parse all supported CSS color syntaxes without an additional dependency.
    const parse = (color) => {
      ctx.fillStyle = color;
      ctx.fillRect(0, 0, 1, 1);
      return [...ctx.getImageData(0, 0, 1, 1).data]
        .slice(0, 3)
        .map((v) => v / 255);
    };
    const active = parse(this.base),
      panel = parse(styles.getPropertyValue('--glass-panel').trim());
    this.color = panel.map((v, i) => v * 0.76 + active[i] * 0.24);
    this.ready = true;
  }

  draw({ x, y, w, h, lift, flow = 0 }, enabled) {
    const visible = enabled && this.available && this.ready && lift > 0.002;
    this.canvas.style.opacity = visible ? String(Math.min(1, lift * 8)) : '0';
    if (!visible) return;
    const gl = this.gl,
      u = this.uniforms;
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.useProgram(this.program);
    gl.uniform2f(u.size, this.width, this.height);
    gl.uniform2f(
      u.center,
      x + w / 2 + this.pad + this.root.clientLeft,
      y + h / 2 + this.pad + this.root.clientTop,
    );
    gl.uniform2f(
      u.radius,
      (w * (1 + 0.3 * lift) * (1 + 0.12 * flow * lift)) / 2,
      (h * (1 + 0.32 * lift) * (1 - 0.13 * flow * lift)) / 2,
    );
    gl.uniform1f(u.lift, lift);
    gl.uniform3fv(u.base, this.color);
    gl.drawArrays(gl.TRIANGLES, 0, 6);
  }
}
