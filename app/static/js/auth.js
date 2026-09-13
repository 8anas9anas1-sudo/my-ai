const s = document.getElementById('stars');
for (let i = 0; i < 55; i++) {
  const el = document.createElement('div');
  el.className = 'star';
  const sz = Math.random() * 2.5 + 0.5;
  el.style.cssText = `width:${sz}px;height:${sz}px;left:${Math.random()*100}%;top:${Math.random()*100}%;--d:${(Math.random()*4+2).toFixed(1)}s;--op:${(Math.random()*0.4+0.2).toFixed(2)};animation-delay:${(Math.random()*5).toFixed(1)}s`;
  s.appendChild(el);
}
