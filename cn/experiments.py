"""Offline CN recipes. Figures, CLI and tables consume this same source of truth.

No recipe claims that a simulator was run. Connectivity panels are expected
outcomes, not captured observations. A future runner can consume the same plan.
"""
import hashlib
import ipaddress
import re

CATALOG = [
    ('devices', 'Packet Tracer, cables and network devices', 'Introduction to Packet Tracer, cables, Hub, Switches and Router.'),
    ('peer', 'Peer to peer connectivity', 'To do peer to peer connectivity, assign IP addresses and share resources.'),
    ('star', 'Network devices and star topology', 'Simulate Hub, Repeater and Switch and connect four computers using star topology.'),
    ('switches', 'Three interconnected switches', 'Create three interconnected switches with two PCs per switch; configure hostname, IP address, subnet mask and default gateway.'),
    ('subnetting', 'FLSM and VLSM subnetting', 'Subnet Class A, B and C networks using FLSM and VLSM.'),
    ('static', 'Router commands, static and default routing', 'Basic router commands, interface addressing, static routing and default routing using two and three routers.'),
    ('rip', 'Dynamic routing using RIP', 'Perform dynamic routing using RIP for automatic route discovery and exchange.'),
    ('ospf', 'Dynamic routing using OSPF', 'Perform dynamic routing using OSPF to discover topology and calculate shortest paths.'),
]


def normalize_profile(profile=None):
    if profile is None:
        profile = {}
    if not isinstance(profile, dict):
        raise ValueError('Student details must be an object.')
    out = {}
    for key in ('name', 'roll', 'section'):
        value = profile.get(key, '')
        if not isinstance(value, str) or len(value) > 100 or any(ord(c) < 32 for c in value):
            raise ValueError(f'Student {key} must be plain text of at most 100 characters.')
        out[key] = value.strip()
    return out


def identify_experiment(aim):
    if not isinstance(aim, str) or not aim.strip() or len(aim) > 4000:
        raise ValueError('Enter a CN experiment aim of 1 to 4000 characters.')
    text = aim.lower()
    # Programming exercises involving networks remain coding exercises.
    if re.search(r'\b(program|socket programming|implement.*algorithm)\b', text):
        return None
    protocols = [key for key in ('ospf', 'rip') if re.search(r'\b' + key + r'(?:v2)?\b', text)]
    if len(protocols) > 1:
        raise ValueError('Separate RIP and OSPF into individual experiment aims.')
    if protocols:
        return protocols[0]
    if re.search(r'\b(flsm|vlsm|subnetting|subnet calculation)\b', text):
        return 'subnetting'
    if re.search(r'\b(static rout\w*|default rout\w*|router commands?|commands of routers?)\b', text):
        return 'static'
    if re.search(r'\b(three|3)\s+(?:interconnected\s+)?switch', text):
        return 'switches'
    if re.search(r'\b(star|repeater)\b', text):
        return 'star'
    if re.search(r'peer[ -]to[ -]peer|\bp2p\b', text):
        return 'peer'
    if re.search(r'\b(introduction|study|identify|explore)\b', text) and re.search(r'packet tracer|cables?|network(?:ing)? devices', text):
        return 'devices'
    return None


def _port(ip, prefix=24, gateway=''):
    iface = ipaddress.ip_interface(f'{ip}/{prefix}')
    return {'ip': str(iface.ip), 'prefix': prefix, 'mask': str(iface.netmask), 'gateway': gateway}


def _device(id_, kind, x, y, ports=None):
    models = {'pc': 'PC-PT', 'router': '2911', 'switch': '2960-24TT', 'hub': 'Hub-PT', 'repeater': 'Repeater-PT'}
    return {'id': id_, 'kind': kind, 'model': models[kind], 'x': x, 'y': y, 'ports': ports or {}}


def _link(a, aport, b, bport, crossover=False):
    return {'a': a, 'aport': aport, 'b': b, 'bport': bport,
            'cable': 'Copper crossover' if crossover else 'Copper straight through'}


def _config(device):
    if device['kind'] not in ('router', 'switch'):
        return []
    lines = ['enable', 'configure terminal', f"hostname {device['id']}"]
    for port, value in device['ports'].items():
        lines += [f'interface {port}', f"ip address {value['ip']} {value['mask']}", 'no shutdown', 'exit']
        if value['gateway']:
            lines += [f"ip default-gateway {value['gateway']}"]
    return lines + ['end', 'copy running-config startup-config']


def _routing(profile, octet):
    devices, links = [], []
    for i in (1, 2, 3):
        lan = f'192.168.{octet+i}'
        ports = {'g0/0': _port(f'{lan}.1')}
        if i == 1:
            ports['g0/1'] = _port(f'10.{octet}.12.1', 30)
        elif i == 2:
            ports['g0/1'] = _port(f'10.{octet}.12.2', 30)
            ports['g0/2'] = _port(f'10.{octet}.23.1', 30)
        else:
            ports['g0/1'] = _port(f'10.{octet}.23.2', 30)
        x = (i - 1) * 4 + 1
        devices += [_device(f'R{i}', 'router', x, 1, ports),
                    _device(f'SW{i}', 'switch', x, 3),
                    _device(f'PC{i-1}', 'pc', x, 5, {'FastEthernet0': _port(f'{lan}.10', gateway=f'{lan}.1')})]
        links += [_link(f'R{i}', 'g0/0', f'SW{i}', 'Fa0/24'), _link(f'PC{i-1}', 'FastEthernet0', f'SW{i}', 'Fa0/1')]
    links += [_link('R1', 'g0/1', 'R2', 'g0/1', True), _link('R2', 'g0/2', 'R3', 'g0/1', True)]
    return devices, links


def validate_plan(plan):
    """Reject conflicting addresses, occupied ports and incompatible link subnets."""
    devices = {d['id']: d for d in plan['devices']}
    if len(devices) != len(plan['devices']):
        raise ValueError('Device names must be unique.')
    addresses, occupied = set(), set()
    for d in devices.values():
        for port, v in d['ports'].items():
            iface = ipaddress.ip_interface(f"{v['ip']}/{v['prefix']}")
            if iface.ip in (iface.network.network_address, iface.network.broadcast_address):
                raise ValueError('A host interface cannot use a network or broadcast address.')
            if v['ip'] in addresses:
                raise ValueError('Duplicate interface IP address.')
            addresses.add(v['ip'])
            if v['gateway'] and ipaddress.ip_address(v['gateway']) not in iface.network:
                raise ValueError('The gateway must be within its host subnet.')
    for link in plan['links']:
        for endpoint in ('a', 'b'):
            id_, port = link[endpoint], link[endpoint+'port']
            if id_ not in devices or (id_, port) in occupied:
                raise ValueError('Invalid or reused link endpoint.')
            occupied.add((id_, port))
        left = devices[link['a']]['ports'].get(link['aport'])
        right = devices[link['b']]['ports'].get(link['bport'])
        if left and right:
            a = ipaddress.ip_interface(f"{left['ip']}/{left['prefix']}").network
            b = ipaddress.ip_interface(f"{right['ip']}/{right['prefix']}").network
            if a != b:
                raise ValueError('Directly connected IP interfaces must share a subnet.')
    return plan


def _subnet_tables(octet):
    tables = []
    for parent, prefix in [('10.0.0.0/8', 10), ('172.16.0.0/16', 18), (f'192.168.{octet}.0/24', 26)]:
        nets = list(ipaddress.ip_network(parent).subnets(new_prefix=prefix))
        tables.append({'title': f'FLSM of {parent}', 'headers': ['Subnet', 'First host', 'Last host', 'Broadcast'],
                       'rows': [[str(n), str(n.network_address+1), str(n.broadcast_address-1), str(n.broadcast_address)] for n in nets]})
    for start in ['10.0.0.0', '172.16.0.0', f'192.168.{octet}.0']:
        addr, rows = int(ipaddress.ip_address(start)), []
        for need, prefix in zip((100, 50, 20, 10), (25, 26, 27, 28)):
            n = ipaddress.ip_network((addr, prefix))
            rows.append([str(need), str(n), str(n.network_address+1), str(n.broadcast_address-1), str(n.broadcast_address)])
            addr += n.num_addresses
        tables.append({'title': f'VLSM starting at {start}', 'headers': ['Hosts', 'Subnet', 'First host', 'Last host', 'Broadcast'], 'rows': rows})
    return tables


def build_experiment(aim, profile=None):
    key = identify_experiment(aim)
    if key is None:
        raise ValueError('CN mode currently supports device basics, peer connectivity, star topology, three switches, subnetting, static/default routing, RIP and OSPF. Split combined aims or use another mode.')
    profile = normalize_profile(profile)
    # A stable private /24 allocation, kept independent of retry/variation seeds.
    seed = '|'.join(profile[k] for k in ('name', 'roll', 'section'))
    octet = 20 + int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16) % 180
    devices, links, configs, tables, procedure = [], [], [], [], []
    description = ''
    verification = []
    if key in ('static', 'rip', 'ospf'):
        devices, links = _routing(profile, octet)
        configs = [{'device': dev['id'], 'commands': _config(dev)} for dev in devices if dev['kind'] == 'router']
        description = {'static': 'Routers forward packets between IP networks. Static routes name a remote network and next hop; a default route matches destinations without a more specific route. A valid return path is required.',
                       'rip': 'RIPv2 exchanges routes and subnet masks using hop count as its metric. Fifteen hops is the maximum reachable distance. Disabling automatic summarisation preserves explicit subnet routes.',
                       'ospf': 'OSPF exchanges link state information and calculates paths using cost. This network uses backbone area 0 and a unique router ID for every router.'}[key]
        procedure = ['Place the three routers, LAN switches and PCs as shown in the topology. Use the exact ports in the connection table.',
                     'Assign host addresses, masks and gateways from the addressing table. Apply each router interface configuration below.',
                     'Use a fresh workspace for this routing protocol; remove static routes and other routing protocols before a dynamic-routing test.',
                     'Run the verification commands, wait for convergence and test connectivity in both directions. Save the project.']
        if key in ('rip', 'ospf'):
            for i, cfg in enumerate(configs, 1):
                lines = ['configure terminal']
                if key == 'rip':
                    lines += ['router rip', 'version 2', 'no auto-summary', 'network 10.0.0.0', f'network 192.168.{octet+i}.0']
                else:
                    lines += ['router ospf 1', f'router-id {i}.{i}.{i}.{i}', f'network 192.168.{octet+i}.0 0.0.0.255 area 0']
                    if i in (1, 2):
                        lines += [f'network 10.{octet}.12.0 0.0.0.3 area 0']
                    if i in (2, 3):
                        lines += [f'network 10.{octet}.23.0 0.0.0.3 area 0']
                lines += ['passive-interface g0/0', 'end', 'copy running-config startup-config']
                cfg['commands'] += lines
            procedure += ['Shut down R2 G0/2, observe route withdrawal after convergence, then restore it with no shutdown. This line topology has no alternate path.']
        else:
            for i,cfg in enumerate(configs,1):
                next_hop = f'10.{octet}.12.2' if i==1 else f'10.{octet}.23.1'
                routes = []
                for remote in (1,2,3):
                    if remote != i:
                        hop = (f'10.{octet}.12.1' if remote==1 else f'10.{octet}.23.2') if i==2 else next_hop
                        routes.append(f'ip route 192.168.{octet+remote}.0 255.255.255.0 {hop}')
                if i in (1,3):
                    net = f'10.{octet}.23.0' if i==1 else f'10.{octet}.12.0'
                    routes.append(f'ip route {net} 255.255.255.252 {next_hop}')
                cfg['commands'] += ['configure terminal'] + routes + ['end', 'copy running-config startup-config']
            configs.append({'device': 'Basic router commands (apply on each router)', 'commands': ['configure terminal', 'enable secret ChangeThisLabSecret', 'line console 0', 'password ChangeThisConsolePassword', 'login', 'exit', 'end', 'show running-config', 'show ip interface brief']})
            configs.append({'device': 'Three-router default variant (fresh copy)', 'commands': [
                'Remove all static routes using no followed by each matching ip route command.',
                'On R1 in global configuration mode:', f'ip route 0.0.0.0 0.0.0.0 10.{octet}.12.2',
                'On R2 in global configuration mode:', f'ip route 192.168.{octet+1}.0 255.255.255.0 10.{octet}.12.1', f'ip route 192.168.{octet+3}.0 255.255.255.0 10.{octet}.23.2',
                'On R3 in global configuration mode:', f'ip route 0.0.0.0 0.0.0.0 10.{octet}.23.1']})
            configs.append({'device': 'Two-router variant (R1 and R2 only)', 'commands': [
                'Keep R1/R2 LAN addresses and their G0/1 transit addresses. Omit R2 G0/2, R3 and LAN 3.',
                'Static variant, in global configuration mode:',
                f'R1: ip route 192.168.{octet+2}.0 255.255.255.0 10.{octet}.12.2',
                f'R2: ip route 192.168.{octet+1}.0 255.255.255.0 10.{octet}.12.1',
                'Default variant, after removing both static routes:',
                f'R1: ip route 0.0.0.0 0.0.0.0 10.{octet}.12.2', f'R2: ip route 0.0.0.0 0.0.0.0 10.{octet}.12.1',
                'Reciprocal defaults can loop traffic for unknown destinations; use only in the isolated lab.']})
        verification = ['show ip interface brief', 'show ip route']
        if key != 'static':
            verification += ['show ip protocols']
        if key == 'ospf':
            verification += ['show ip ospf neighbor']
        verification += [f'PC0: ping 192.168.{octet+3}.10', f'PC2: ping 192.168.{octet+1}.10']
    elif key == 'subnetting':
        description = 'FLSM divides a parent network into equal sized subnets. VLSM allocates different subnet sizes to meet unequal host demands. Class A, B and C are historical categories; CIDR prefixes define the actual network boundary.'
        tables = _subnet_tables(octet)
        procedure = ['For each parent prefix, borrow two bits to create four equal FLSM subnets.',
                     'For ordinary LAN subnets, usable hosts = 2^h - 2. Determine the network, host range and broadcast address.',
                     'For VLSM, allocate demands 100, 50, 20 and 10 in descending order using /25, /26, /27 and /28.',
                     'Check every block boundary and confirm allocations do not overlap. The /31 and /32 special cases are outside these LAN examples.']
        verification = ['Check each first host = network + 1.', 'Check each last host = broadcast - 1.', 'Check the next block begins after the previous broadcast.']
    else:
        description = {'devices': 'Packet Tracer provides a workspace for arranging, configuring and observing network devices. Hubs repeat signals to other ports, switches learn MAC addresses, and routers forward packets between networks.',
                       'peer': 'Two hosts on one subnet can communicate without a router. ARP resolves local IPv4 destinations to MAC addresses and ping checks ICMP reachability.',
                       'star': 'A star topology connects each host to a central switch. The switch floods broadcasts and unknown unicasts, then forwards learned unicast frames toward the destination port. Hubs repeat signals; repeaters regenerate them.',
                       'switches': 'Interconnected Layer 2 switches extend a LAN. VLAN interfaces provide management addresses; a default gateway is used for management traffic outside the subnet. Spanning tree prevents forwarding loops on redundant links.'}[key]
        lan = f'192.168.{octet}'
        count = 2 if key in ('peer','devices') else 6 if key=='switches' else 4
        for i in range(count):
            x = 1 + (i % 3)*4 if key=='switches' else (1 if i%2==0 else 9)
            y = 5 if key=='switches' else (1 if i<2 else 5)
            if key=='switches':
                x += -.7 if i<3 else .7
            devices.append(_device(f'PC{i}', 'pc', x, y, {'FastEthernet0': _port(f'{lan}.{11+i}', gateway=f'{lan}.1' if key=='switches' else '')}))
        if key == 'peer':
            devices[0].update(x=2,y=3);devices[1].update(x=8,y=3)
            links = [_link('PC0','FastEthernet0','PC1','FastEthernet0',True)]
        elif key == 'switches':
            for i in (1,2,3):
                devices.append(_device(f'SW{i}','switch',1+(i-1)*4,2,{'vlan 1':_port(f'{lan}.{i+1}',gateway=f'{lan}.1')}))
                links += [_link(f'PC{i-1}','FastEthernet0',f'SW{i}','Fa0/1'), _link(f'PC{i+2}','FastEthernet0',f'SW{i}','Fa0/2')]
            links += [_link('SW1','Fa0/23','SW2','Fa0/23',True),_link('SW2','Fa0/24','SW3','Fa0/23',True),_link('SW3','Fa0/24','SW1','Fa0/24',True)]
            devices.append(_device('R1','router',1,0,{'g0/0':_port(f'{lan}.1')}));links.append(_link('R1','g0/0','SW1','Fa0/3'))
        else:
            devices.append(_device('SW1','switch',5,3))
            links = [_link(f'PC{i}','FastEthernet0','SW1',f'Fa0/{i+1}') for i in range(count)]
            if key == 'devices':
                devices += [_device('Hub0','hub',2,1),_device('R1','router',8,1,{'g0/0':_port(f'{lan}.1')})]
                devices[0].update(x=1,y=5);devices[1].update(x=9,y=5)
                links += [_link('Hub0','Port 0','SW1','Fa0/23',True),_link('R1','g0/0','SW1','Fa0/24')]
        configs = [{'device': dev['id'],'commands':_config(dev)} for dev in devices if dev['kind'] in ('router','switch') and dev['ports']]
        procedure = ['Place and connect the devices using the exact ports and cables in the connection table.',
                     'Open each PC Desktop > IP Configuration. Assign its static IP address, subnet mask and gateway from the addressing table.',
                     'Apply any device CLI configurations below, wait for links and spanning tree to settle, then test the listed destinations.',
                     'Save the workspace. Repeat the first ping if ARP resolution causes an initial timeout.']
        verification = [f"PC0: ping {devices[count-1]['ports']['FastEthernet0']['ip']}"]
        if key in ('star','switches'):
            verification += ['Switch: show mac address-table']
        if key=='switches':
            verification += ['Switch: show ip interface brief','Switch: show spanning-tree vlan 1']
        if key=='devices':
            procedure = ['Locate the device palette, workspace, Connections and Simulation mode.',
                         'Identify Hub-PT, 2960 switch, 2911 router and PC-PT. Compare their functions.',
                         'Use straight through for PC to switch and router to switch; crossover for direct PC to PC or switch to switch.',
                         'Use a console cable from PC RS232 to the router Console for local management. Fibre and serial links require compatible ports/modules.',
                         'Build the displayed topology, apply its IP settings and enable router G0/0. Save the workspace.']
        if key=='star':
            procedure += ['In a separate copy replace the switch with Hub-PT and compare frame propagation in Simulation mode. Test Repeater-PT with two hosts if the device is available; otherwise record that limitation.']
        if key=='peer':
            procedure += ['Folder sharing requires real computers or VMs: Packet Tracer PC-PT does not implement Windows SMB sharing. On a trusted private LAN share a test folder with a specific user, open the UNC path from the other host and verify access separately.']
    plan = validate_plan({'devices':devices,'links':links})
    title = next(t for k,t,_ in CATALOG if k==key)
    address_rows = [[dev['id'],port,v['ip'],v['mask'],v['gateway'] or 'Not required'] for dev in devices for port,v in dev['ports'].items()]
    requirements = ['Cisco Packet Tracer', ', '.join(f"{sum(d['kind']==kind for d in devices)} {kind}{'s' if sum(d['kind']==kind for d in devices)>1 else ''}" for kind in ('router','switch','hub','pc') if any(d['kind']==kind for d in devices)), 'Copper Ethernet cables'] if devices else ['Subnet calculation worksheet or calculator']
    pc_devices = [dev for dev in devices if dev['kind']=='pc']
    figures = [{'id':'topology','kind':'topology','caption':'Network Topology','source':'generated'}] if devices else [{'id':'subnets','kind':'subnets','caption':'FLSM and VLSM Allocation','source':'generated'}]
    if pc_devices:
        figures += [{'id':'ip','kind':'ip','device':pc_devices[0]['id'],'caption':f"IP Configuration of {pc_devices[0]['id']}",'source':'generated'}]
        figures += [{'id':'connectivity','kind':'connectivity','device':pc_devices[0]['id'],'destination':next(iter(pc_devices[-1]['ports'].values()))['ip'],'caption':'Expected Connectivity Output','source':'generated'}]
    if key in ('static','rip','ospf','switches'):
        figures += [{'id':'verification','kind':'verification','caption':'Expected Routing Output' if key!='switches' else 'Expected Switch Verification','source':'generated'}]
    return {'mode':'cn','schema_version':1,'template_id':key,'title':title,'aim':aim.strip(),'concept':description,
            'profile':profile,'topology':plan,'address_rows':address_rows,'requirements':requirements,'procedure':procedure,
            'configurations':configs,'tables':tables,'verification':verification,'figures':figures,
            'result':'Expected: '+ ('The allocations meet host requirements without overlap.' if key=='subnetting' else 'The configured devices communicate as specified when the verification tests pass.'),
            'execution':{'status':'not_run','engine':None},'caption':title}
