/* 240px renderer. No networking, device operations or changes to approved M. */
(() => {
  'use strict';
  const state={period:18};
  const outline=[[-22,-24],[-10,-24],[0,-5],[10,-24],[22,-24],[22,24],[10,24],[10,-2],[0,14],[-10,-2],[-10,24],[-22,24]];
  function letter(ctx,cx,cy,scale,seconds) {
    const angle=.45+seconds/state.period*Math.PI*2;
    const ca=Math.cos(angle),sa=Math.sin(angle),tilt=-.12;
    const bob=Math.sin(seconds/4.5*Math.PI*2)*1.2;
    const project=(p,z)=> {
      const x=p[0]*ca+z*sa, depth=-p[0]*sa+z*ca;
      const yy=p[1]*Math.cos(tilt)-depth*Math.sin(tilt);
      const zz=p[1]*Math.sin(tilt)+depth*Math.cos(tilt);
      const perspective=180/(180-zz);
      return {x:cx+x*scale*perspective,y:cy+yy*scale*perspective+bob,z:zz};
    };
    ctx.save();
    const halo=ctx.createRadialGradient(cx,cy+6,0,cx,cy+6,36);
    halo.addColorStop(0,'rgba(0,160,255,'+(.10+.035*Math.sin(seconds*Math.PI/2.25))+')');halo.addColorStop(1,'rgba(0,100,200,0)');
    ctx.fillStyle=halo;ctx.fillRect(cx-39,cy-33,78,75);
    ctx.fillStyle='rgba(12,151,218,.18)';ctx.beginPath();ctx.ellipse(cx,cy+25*scale+5,23*scale,3*scale,0,0,Math.PI*2);ctx.fill();
    const front=outline.map(p=>project(p,5)),back=outline.map(p=>project(p,-5));
    const faces=[];
    function face(points,type,shade) {faces.push({points,type,shade,depth:points.reduce((s,p)=>s+p.z,0)/points.length});}
    face(front,'front',Math.max(.25,.65+.35*ca));face(back,'back',Math.max(.25,.65-.35*ca));
    for(let i=0;i<outline.length;i++) {
      const j=(i+1)%outline.length,dx=outline[j][0]-outline[i][0],dy=outline[j][1]-outline[i][1];
      face([front[i],back[i],back[j],front[j]],'side',.4+.3*Math.abs(dy/Math.hypot(dx,dy)*sa));
    }
    faces.sort((a,b)=>a.depth-b.depth);
    for(const f of faces) {
      ctx.beginPath();f.points.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.closePath();
      const g=ctx.createLinearGradient(cx-15,cy-25,cx+18,cy+24);
      if(f.type==='side') {g.addColorStop(0,'#157ba9');g.addColorStop(1,'#042e52');}
      else {g.addColorStop(0,'#cffbff');g.addColorStop(.20,'#52defb');g.addColorStop(.55,'#138ac7');g.addColorStop(1,'#07517e');}
      ctx.fillStyle=g;ctx.globalAlpha=.8+.2*f.shade;ctx.fill();ctx.globalAlpha=1;
      ctx.strokeStyle=f.type==='side'?'rgba(69,190,235,.55)':'rgba(174,242,255,.75)';ctx.lineWidth=.65;ctx.stroke();
    }
    ctx.restore();
  }
  const white='#edfbff',soft='#a9c8df',blue='#40c8f4',muted='#7993ad';
  let zone=null,hours,date,day;
  function formatters(timeZone) {
    timeZone=timeZone||'UTC';if(zone===timeZone)return;
    try {hours=new Intl.DateTimeFormat('en-GB',{timeZone,hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'});
      date=new Intl.DateTimeFormat('en-GB',{timeZone,day:'2-digit',month:'2-digit',year:'numeric'});
      day=new Intl.DateTimeFormat('ru-RU',{timeZone,weekday:'short'});zone=timeZone;
    } catch {formatters('UTC');}
  }
  function create(canvas) {
    canvas.width=240;canvas.height=240;
    const ctx=canvas.getContext('2d');let boxes=[],lastIP=null,ipHideStart=null;
    function text(s,x,y,size,color=white,weight=400,align='left',max=218,ellipsis=true,fit=false) {
      ctx.save();ctx.font='700 '+size+'px "Trebuchet MS",Arial,sans-serif';ctx.fillStyle=color;
      ctx.textAlign=align;ctx.textBaseline='top';
      const original=String(s);s=original;
      if(fit){while(ctx.measureText(s).width>max&&size>10){size-=.5;ctx.font='700 '+size+'px "Trebuchet MS",Arial,sans-serif';}}
      while(ctx.measureText(s).width>max&&s.length>1)s=ellipsis?s.slice(0,-2)+'…':s.slice(0,-1);
      const m=ctx.measureText(s);ctx.fillText(s,x,y);
      const transform=ctx.getTransform();
      boxes.push({text:s,original,x:transform.e+x-m.actualBoundingBoxLeft,right:transform.e+x+m.actualBoundingBoxRight,
                  y:transform.f+y-m.actualBoundingBoxAscent,bottom:transform.f+y+m.actualBoundingBoxDescent});ctx.restore();
    }
    function rounded(x,y,w,h,r,fill,stroke) {
      ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fillStyle=fill;ctx.fill();
      if(stroke){ctx.strokeStyle=stroke;ctx.lineWidth=.7;ctx.stroke();}
    }
    function digitClassic(value,x,y,color) {
      const codes=['abcdef','bc','abged','abgcd','fgbc','afgcd','afgecd','abc','abcdefg','abfgcd'];
      const poly={a:[[3,0],[27,0],[30,3],[24,7],[6,7],[0,3]],g:[[5,22],[25,22],[29,25],[25,28],[5,28],[1,25]],d:[[5,43],[25,43],[30,47],[27,50],[3,50],[0,47]],
        f:[[0,5],[7,8],[7,20],[3,24],[0,21]],b:[[30,5],[23,8],[23,20],[27,24],[30,21]],e:[[0,29],[3,26],[7,30],[7,42],[0,45]],c:[[30,29],[27,26],[23,30],[23,42],[30,45]]};
      for(const key of codes[Number(value)]){ctx.beginPath();poly[key].forEach((p,i)=>i?ctx.lineTo(x+p[0],y+p[1]):ctx.moveTo(x+p[0],y+p[1]));ctx.closePath();ctx.fillStyle=color;ctx.fill();}
    }
    function digit(value,x,y,color,style='classic') {
      if(style==='classic'){digitClassic(value,x,y,color);return;}
      ctx.save();ctx.fillStyle=color;
      if(style==='rounded'){
        const boxes=[[4,0,23,7,3],[23,8,7,14,3],[23,30,7,14,3],[4,44,23,7,3],[1,30,7,14,3],[1,8,7,14,3],[4,22,23,7,3]];
        const codes=['abcdef','bc','abged','abgcd','fgbc','afgcd','afgecd','abc','abcdefg','abfgcd'];
        for(const key of codes[Number(value)]){const b=boxes[key.charCodeAt(0)-97];ctx.beginPath();ctx.roundRect(x+b[0],y+b[1],b[2],b[3],b[4]);ctx.fill();}
      } else {
        ctx.font='700 51px "Trebuchet MS",Arial,sans-serif';ctx.textBaseline='alphabetic';ctx.textAlign='left';
        const m=ctx.measureText(String(value)),height=m.actualBoundingBoxAscent+m.actualBoundingBoxDescent;
        ctx.translate(x,y);ctx.scale(31/m.width,51/height);ctx.fillText(String(value),0,m.actualBoundingBoxAscent);
      }
      ctx.restore();
    }
    function weatherIcon(kind,x,y) {
      ctx.save();ctx.translate(x,y);
      if(kind==='unknown'){text('—',0,-9,24,muted,400,'center',36);ctx.restore();return;}
      if(kind==='sun') {
        ctx.strokeStyle='#8ee8ff';ctx.lineWidth=1.5;
        for(let i=0;i<8;i++){const a=i*Math.PI/4;ctx.beginPath();ctx.moveTo(Math.cos(a)*12,Math.sin(a)*12);ctx.lineTo(Math.cos(a)*16,Math.sin(a)*16);ctx.stroke();}
        const g=ctx.createRadialGradient(-3,-4,1,0,0,10);g.addColorStop(0,'#e8fdff');g.addColorStop(1,'#39a8db');ctx.fillStyle=g;ctx.beginPath();ctx.arc(0,0,10,0,Math.PI*2);ctx.fill();
      } else if(kind==='dew') {
        const g=ctx.createLinearGradient(-8,-14,9,12);g.addColorStop(0,'#ddfaff');g.addColorStop(1,'#1375b0');ctx.fillStyle=g;
        ctx.beginPath();ctx.moveTo(0,-15);ctx.bezierCurveTo(19,5,10,14,0,14);ctx.bezierCurveTo(-10,14,-19,5,0,-15);ctx.fill();
      } else {
        for(const [cx,cy,r] of [[-10,1,8],[-4,-7,10],[6,-4,10],[13,3,7],[-2,4,11]]) {
          const g=ctx.createRadialGradient(cx-3,cy-4,0,cx,cy,r);g.addColorStop(0,'#effcff');g.addColorStop(.6,'#b1d5fb');g.addColorStop(1,'#4e7eb0');ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,r,0,Math.PI*2);ctx.fill();
        }
        if(kind==='snow'){text('❄',-8,10,14,'#d1f5ff',400,'center',18);text('❄',9,13,11,'#a5e6ff',400,'center',16);}
        if(kind==='rain'){ctx.strokeStyle='#60d6ff';ctx.lineWidth=2;for(const dx of [-10,0,10]){ctx.beginPath();ctx.moveTo(dx,13);ctx.lineTo(dx-3,19);ctx.stroke();}}
      }
      ctx.restore();
    }
    function render(data,seconds=0,at=new Date(),nowMs=Date.now()) {
      formatters(data.timezone);
      boxes=[];const palette=data.palette||{hour_from:'#edfbff',hour_to:'#168dbf',background_from:'#020911',background_to:'#07304a'};
      const background=ctx.createLinearGradient(0,0,240,240);background.addColorStop(0,palette.background_from);background.addColorStop(1,palette.background_to);ctx.fillStyle=background;ctx.fillRect(0,0,240,240);
      ctx.save();ctx.translate(0,-2);
      for(let i=0;i<16;i++){const x=155+(i*31)%78,y=94+(i*23)%113;ctx.fillStyle=i%3===0?'#166294':'#10324a';ctx.fillRect(x,y,1,1);}
      const showIP=data.show_ip!==false;
      if(lastIP===true&&!showIP)ipHideStart=nowMs;lastIP=showIP;
      if(showIP)text('IP '+(data.ip_text||data.device_ip||'—'),10,98,10.5,soft,400,'left',176);
      else if(ipHideStart!==null&&nowMs-ipHideStart<800){const progress=(nowMs-ipHideStart)/800,frame=Math.floor((nowMs-ipHideStart)/80);let noise='';for(let i=0;i<16;i++)noise+=(i/16<1-progress)?'0123456789ABCDEF'[(frame*7+i*11)%16]:' ';ctx.save();ctx.globalAlpha=1-progress;text(noise,10,98,10.5,blue,400,'left',176);ctx.restore();}
      const w=data.weather||{},kind=w.available?w.kind:'unknown',feelsPhase=Math.floor(seconds/5)%2===1;
      const degree=feelsPhase?w.feels_c:w.temperature_c;
      const temp=w.available&&Number.isFinite(degree)?(degree>0?'+':'')+degree+'°C':'—';
      weatherIcon(kind,199,27);
      text(temp,198,56,20,white,700,'center',80,false,true);
      text(w.available?(feelsPhase?'Ощущается':w.description):'Нет погоды',198,80,13,soft,700,'center',80,false,true);
      const pieces=hours.format(at).split(':');
      const clockGradient=ctx.createLinearGradient(10,22,141,70);clockGradient.addColorStop(0,palette.hour_from);clockGradient.addColorStop(1,palette.hour_to);
      function compactDigit(value,x,y){ctx.save();ctx.translate(x,y);ctx.scale(26/31,43/51);digit(value,0,0,clockGradient,data.clock_style);ctx.restore();}
      compactDigit(pieces[0][0],10,22);compactDigit(pieces[0][1],39,22);compactDigit(pieces[1][0],73,22);compactDigit(pieces[1][1],102,22);
      ctx.fillStyle='#9ccee9';ctx.fillRect(68,32,4,4);ctx.fillRect(68,49,4,4);
      text(pieces[2],132,46,18,soft,700,'left',24,false,true);
      text(date.format(at),10,80,14,white,700,'left',110,false,true);text(day.format(at).replace('.','').toUpperCase(),122,80,14,blue,700,'left',24,false,true);
      rounded(9,116,157,91,7,'#03101b',palette.frame||'#105070');
      ctx.strokeStyle=blue;ctx.lineWidth=1;ctx.beginPath();ctx.roundRect(18,126,21,23,4);ctx.stroke();
      ctx.strokeStyle=white;ctx.beginPath();ctx.moveTo(23,132);ctx.lineTo(28,137);ctx.lineTo(23,142);ctx.stroke();
      text('Codex',45,120,26,white,700,'left',111,false,true);
      const raw=data.usage;
      const fresh=raw&&Number.isFinite(raw.remaining_percent)&&raw.remaining_percent>=0&&raw.remaining_percent<=100&&Number.isFinite(raw.observed_at)&&nowMs/1000-raw.observed_at<=600&&nowMs/1000-raw.observed_at>=-5&&raw.resets_at>nowMs/1000;
      const pct=fresh?Math.round(raw.remaining_percent):null;
      rounded(18,164,75,7,3,palette.frame||'#105070');
      if(pct===null)text('Нет данных',18,178,16,soft,700,'left',139,false,true);
      else {if(pct>0)rounded(18,164,75*pct/100,7,Math.min(3,75*pct/200),blue);const left=Math.max(0,Math.floor(raw.resets_at-nowMs/1000)),days=Math.floor(left/86400),hours=Math.floor(left%86400/3600);const body=days?days+' д. '+hours+' ч.':left>=3600?Math.floor(left/3600)+' ч. '+Math.floor(left%3600/60)+' м.':Math.ceil(left/60)+' мин.';text(body,18,181,20,soft,700,'left',139,false,true);text(pct+'%',98,153,34,white,700,'left',61,false,true);}
      letter(ctx,200,170,.88,seconds);
      const c=data.codex||{};let footer='Codex не подключён',color=soft;
      if(c.status==='WORKING'){const n=c.working_count||0;footer=(n===1?'Работает: ':'Работают: ')+n+' '+(n===1?'сессия':n>=2&&n<=4?'сессии':'сессий');color=blue;}
      else if(c.status==='WARNING'){footer=Math.floor(seconds/5)%2===1&&c.label?c.label+(c.working_count>0?' · ещё '+c.working_count:''):'Нужно подтверждение';color='#ffd06c';}
      else if(c.status==='CLOCK'){footer='Готов к работе';color='#73efae';}
      else if(c.status==='DONE'){footer='Задачи завершены';color='#73efae';}
      else if(c.status==='DIAGNOSTIC'||c.status==='UNKNOWN'){footer='Проверяем состояние';color='#ffd06c';}
      const replyActive=!!(data.reply_ready&&Number.isInteger(data.reply_ready.id)&&data.reply_ready.id>0&&nowMs/1000>=data.server_time_ms/1000-1&&nowMs-data.server_time_ms<data.reply_ready.ttl_ms&&['CLOCK','WORKING'].includes(c.status));if(replyActive){footer='Ответ готов';color='#8be8ff';}
      rounded(9,213,222,25,6,'#03101b',replyActive?'#54c5d2':(palette.frame||'#105070'));
      ctx.fillStyle=color;ctx.beginPath();ctx.arc(20,223.5,3,0,Math.PI*2);ctx.fill();
      text(footer,120,216,12,color,700,'center',193);
      ctx.restore();
      return {boxes:boxes.slice(),quota:pct,footer,seconds:pieces[2]};
    }
    return {render};
  }
  globalThis.MadyarHome={create};
})();



