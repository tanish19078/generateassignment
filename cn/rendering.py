"""Deterministic topology, IP settings and expected-output figures using Pillow."""
import base64
import io
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


def font(size=22, bold=False, mono=False):
    family = 'DejaVuSansMono' if mono else 'DejaVuSans'
    name = family + ('-Bold' if bold else '') + '.ttf'
    paths = [Path('/usr/share/fonts/truetype/dejavu')/name,
             Path('/usr/share/fonts/truetype/liberation2')/('LiberationMono-Regular.ttf' if mono else 'LiberationSans-Regular.ttf')]
    for path in paths:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def canvas(exp, title, height=720, expected=False):
    im=Image.new('RGB',(1300,height),'white');draw=ImageDraw.Draw(im)
    draw.rectangle((0,0,1300,76),fill='#123e56')
    draw.text((30,20),title,font=font(28,True),fill='white')
    name=' | '.join(x for x in (exp['profile']['name'],exp['profile']['roll'],exp['profile']['section']) if x)
    name = name or 'Computer Networks Practical'
    while draw.textlength(name,font=font(20)) > 1230:
        name = name[:-2].rstrip('…')+'…'
    draw.text((30,94),name,font=font(20),fill='#374151')
    if expected:
        draw.text((30,height-36),'Expected output — run the verification commands to confirm.',font=font(18),fill='#5a3a00')
    return im,draw


def _icon(draw,x,y,kind,label,compact=False):
    fill,edge='#88bfce','#245c72'
    if kind=='pc':
        draw.rounded_rectangle((x-27,y-23,x+27,y+13),radius=3,fill=fill,outline=edge,width=2)
        draw.rectangle((x-20,y-17,x+20,y+7),fill='#e5f7fa',outline=edge)
        draw.line((x,y+13,x,y+25),fill=edge,width=4)
        draw.polygon([(x-33,y+32),(x+33,y+32),(x+22,y+22),(x-22,y+22)],fill=fill,outline=edge)
    elif kind=='router':
        draw.ellipse((x-38,y-22,x+38,y+22),fill=fill,outline=edge,width=2)
        for sign in (-1,1):
            draw.line((x,y,x+sign*22,y-sign*10),fill='white',width=3)
            draw.line((x+sign*22,y-sign*10,x+sign*12,y-sign*10),fill='white',width=3)
    else:
        draw.polygon([(x-39,y-16),(x+30,y-16),(x+40,y+14),(x-30,y+14)],fill=fill,outline=edge)
        for dx in (-24,-10,4,18):draw.rectangle((x+dx,y-6,x+dx+8,y+3),fill=edge)
    for i,line in enumerate(label.split('\n')):
        textfont=font(16 if compact else 18)
        bbox=draw.textbbox((x,y+39+i*23),line,font=textfont,anchor='mt')
        draw.rectangle((bbox[0]-2,bbox[1],bbox[2]+2,bbox[3]),fill='white')
        draw.text((x,y+39+i*23),line,font=textfont,fill='#202b35',anchor='mt')


def topology(exp):
    im,draw=canvas(exp,'Network Topology',850)
    devices=exp['topology']['devices'];by_id={d['id']:d for d in devices}
    # Leave room above and below every device for addresses and port labels.
    xy={d['id']:(110+int(d['x']*112),185+int(d['y']*94)) for d in devices}
    for link in exp['topology']['links']:
        a,b=xy[link['a']],xy[link['b']]
        # The redundant SW1/SW3 edge must not disappear behind SW2.
        redundant={link['a'],link['b']}=={'SW1','SW3'} and exp['template_id']=='switches'
        points=[a,(a[0],a[1]-65),(b[0],b[1]-65),b] if redundant else [a,b]
        draw.line(points,fill='#3c4855',width=3)
        if by_id[link['a']]['kind']=='router' and by_id[link['b']]['kind']=='router':
            pa=by_id[link['a']]['ports'][link['aport']]
            import ipaddress
            label=str(ipaddress.ip_interface(pa['ip']+'/'+str(pa['prefix'])).network)
            draw.text(((a[0]+b[0])/2,(a[1]+b[1])/2-32),label,font=font(18),fill='#253c49',anchor='mt')
    for dev in devices:
        parts=[dev['id'],dev['model']]
        if dev['ports']:
            port=next(iter(dev['ports'].values()));parts.append(port['ip'] if exp['template_id']=='switches' and dev['kind']=='pc' else f"{port['ip']}/{port['prefix']}")
        _icon(draw,*xy[dev['id']],dev['kind'],'\n'.join(parts),compact=exp['template_id']=='switches' and dev['kind']=='pc')
    draw.text((30,814),'Ports and cable types are listed in the connection table.',font=font(18),fill='#4b5563')
    return im


def ip_panel(exp, figure):
    dev=next(d for d in exp['topology']['devices'] if d['id']==figure['device'])
    port,value=next(iter(dev['ports'].items()))
    im,draw=canvas(exp,f"{dev['id']} — IP Configuration",580)
    draw.text((55,156),f'Interface: {port}     Addressing: Static',font=font(24,True),fill='#123e56')
    for i,(label,val) in enumerate([('IPv4 Address',value['ip']),('Subnet Mask',value['mask']),('Default Gateway',value['gateway'] or 'Not required'),('DNS Server','Not required for this experiment')]):
        y=228+i*70
        draw.text((55,y),label,font=font(24),fill='#202b35')
        draw.rounded_rectangle((420,y-12,1230,y+43),radius=5,outline='#bcc9d1',width=2)
        draw.text((440,y),val,font=font(24),fill='#202b35')
    return im


def text_panel(exp,title,lines):
    wrapped=[]
    for line in lines:wrapped.extend(textwrap.wrap(line,width=90,replace_whitespace=False) or [''])
    im,draw=canvas(exp,title,max(550,210+len(wrapped)*33),expected=True)
    draw.rounded_rectangle((25,142,1275,im.height-65),radius=5,fill='#121b25')
    for i,line in enumerate(wrapped):draw.text((48,162+i*33),line,font=font(22,mono=True),fill='#e6f2f4')
    return im


def render_figure(exp,figure):
    kind=figure['kind']
    if kind=='topology':im=topology(exp)
    elif kind=='ip':im=ip_panel(exp,figure)
    elif kind=='connectivity':
        dest=figure['destination']
        im=text_panel(exp,'Expected Connectivity Output',[
            f"{figure['device']}> ping {dest}",'',
            'Expected: ICMP echo requests receive replies from the destination.',
            'An initial timeout may occur while ARP resolves; repeat the test.',
            'Measure packet loss and latency in the actual Packet Tracer run.',
        ])
    elif kind=='verification':
        key=exp['template_id'];lines=list(exp['verification'])+['']
        if key in ('rip','ospf','static'):
            routers=[dev for dev in exp['topology']['devices'] if dev['kind']=='router']
            r1,r2,r3=routers
            remote=r3['ports']['g0/0'];hop=r2['ports']['g0/1']['ip']
            import ipaddress
            net=ipaddress.ip_interface(remote['ip']+'/24').network
            indicator={'rip':'R','ospf':'O','static':'S'}[key]
            lines += [f'Expected on R1: {indicator} route to {net} via {hop}.']
            if key=='ospf':lines += ['Expected neighbors: R1 has 1, R2 has 2, R3 has 1.']
            if key=='rip':lines += ['Expected R1 metric to LAN 3: 2 hops.']
        else:lines += ['Expected: VLAN 1 management interfaces are up/up.', 'Expected: one redundant switch path is blocked by STP.']
        im=text_panel(exp,figure['caption'],lines)
    elif kind=='subnets':
        lines=['VLSM host demand -> prefix -> usable capacity','100 hosts -> /25 -> 126 usable','50 hosts  -> /26 -> 62 usable','20 hosts  -> /27 -> 30 usable','10 hosts  -> /28 -> 14 usable','', 'Full FLSM/VLSM address ranges are in the calculation tables.']
        im=text_panel(exp,'FLSM and VLSM Allocation',lines)
    else:raise ValueError('Unknown CN figure kind.')
    buf=io.BytesIO()
    try:im.save(buf,format='PNG',optimize=True);return buf.getvalue()
    finally:im.close();buf.close()


def with_previews(exp):
    for figure in exp['figures']:
        figure['data_url']='data:image/png;base64,'+base64.b64encode(render_figure(exp,figure)).decode('ascii')
    return exp
