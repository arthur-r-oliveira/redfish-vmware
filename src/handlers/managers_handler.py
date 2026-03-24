#!/usr/bin/env python3
"""
Managers Handler
Handles Redfish Managers endpoints for BMC management.
"""

import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from models.redfish_schemas import RedfishModels
from vmware.media_operations import InsertMediaError

logger = logging.getLogger(__name__)


class ManagersHandler:
    """Handler for Redfish Managers endpoints"""
    
    def __init__(self, vm_configs: Dict, redfish_handler):
        self.vm_configs = vm_configs
        self._redfish = redfish_handler
        logger.info("🔧 Managers handler initialized")
    
    def handle_get(self, request_handler, path: str):
        """Handle GET requests for Managers"""
        if path == '/redfish/v1/Managers':
            # Managers collection
            data = RedfishModels.get_managers_collection(list(self.vm_configs.keys()))
            self._send_json_response(request_handler, 200, data)
        elif '/redfish/v1/Managers/' in path:
            # Individual manager
            manager_id = self._extract_manager_id(path)
            if manager_id:
                vm_name = manager_id.replace('-bmc', '') if manager_id.endswith('-bmc') else manager_id
                if vm_name in self.vm_configs:
                    if '/VirtualMedia' in path:
                        self._handle_virtual_media_get(request_handler, manager_id, path)
                    elif '/EthernetInterfaces' in path:
                        self._handle_ethernet_interfaces_get(request_handler, manager_id, path)
                    else:
                        data = self._get_manager_info(manager_id)
                        self._send_json_response(request_handler, 200, data)
                else:
                    self._send_error_response(request_handler, 404, "Manager not found")
            else:
                self._send_error_response(request_handler, 404, "Manager not found")
        else:
            self._send_error_response(request_handler, 404, "Not Found")
    
    def handle_post(self, request_handler, path: str):
        """Handle POST requests for Managers (VirtualMedia actions)."""
        path_only = path.split('?')[0]
        if '/VirtualMedia/' not in path_only or '/Actions/' not in path_only:
            self._send_error_response(request_handler, 404, "Not Found")
            return
        manager_id = self._extract_manager_id(path)
        if not manager_id:
            self._send_error_response(request_handler, 404, "Manager not found")
            return
        vm_name = manager_id.replace('-bmc', '') if manager_id.endswith('-bmc') else manager_id
        if vm_name not in self.vm_configs:
            self._send_error_response(request_handler, 404, "Manager not found")
            return
        client = self._redfish.get_vmware_client(vm_name)
        if not client:
            self._send_error_response(request_handler, 503, "VMware client not available")
            return
        if path_only.endswith('/Actions/VirtualMedia.InsertMedia'):
            self._handle_insert_media(request_handler, vm_name, client)
        elif path_only.endswith('/Actions/VirtualMedia.EjectMedia'):
            self._handle_eject_media(request_handler, vm_name, client)
        else:
            self._send_error_response(request_handler, 404, "Not Found")
    
    def _handle_insert_media(self, request_handler, vm_name: str, client):
        """Handle VirtualMedia.InsertMedia: parse Image URL and mount ISO from URL."""
        try:
            content_length = int(request_handler.headers.get('Content-Length', 0))
            if content_length <= 0:
                self._send_error_response(request_handler, 400, "Missing request body")
                return
            body = request_handler.rfile.read(content_length)
            data = json.loads(body.decode('utf-8'))
            image_url = data.get('Image') or data.get('image')
            if not image_url or not str(image_url).strip():
                self._send_error_response(request_handler, 400, "Image URL is required")
                return
            image_url = str(image_url).strip()
            write_protected = data.get('WriteProtected', True)
            vm_config = self.vm_configs.get(vm_name) or {}
            client.mount_iso_from_url(
                vm_name, image_url, write_protected=write_protected,
                datastore_name=vm_config.get('virtual_media_datastore'),
                virtual_media_folder=vm_config.get('virtual_media_folder')
            )
            request_handler.send_response(204)
            request_handler.end_headers()
        except InsertMediaError as e:
            msg = str(e)
            logger.warning(f"InsertMedia failed for {vm_name}: {msg}")
            self._send_error_response(request_handler, 500, f"Failed to insert virtual media: {msg}")
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in InsertMedia body: {e}")
            self._send_error_response(request_handler, 400, "Invalid JSON body")
        except Exception as e:
            logger.error(f"InsertMedia error for {vm_name}: {e}", exc_info=True)
            self._send_error_response(request_handler, 500, f"Internal server error: {e}")
    
    def _handle_eject_media(self, request_handler, vm_name: str, client):
        """Handle VirtualMedia.EjectMedia: unmount ISO."""
        try:
            success = client.unmount_iso(vm_name)
            if success:
                request_handler.send_response(204)
                request_handler.end_headers()
            else:
                self._send_error_response(request_handler, 500, "Failed to eject virtual media")
        except Exception as e:
            logger.error(f"EjectMedia error for {vm_name}: {e}", exc_info=True)
            self._send_error_response(request_handler, 500, "Internal server error")
    
    def _extract_manager_id(self, path: str) -> Optional[str]:
        """Extract manager ID from path"""
        parts = path.split('/')
        if 'Managers' in parts:
            managers_index = parts.index('Managers')
            if len(parts) > managers_index + 1:
                return parts[managers_index + 1]
        return None
    
    def _get_manager_info(self, manager_id: str) -> Dict:
        """Get manager information"""
        vm_name = manager_id.replace('-bmc', '') if manager_id.endswith('-bmc') else manager_id
        
        return {
            '@odata.type': '#Manager.v1_13_0.Manager',
            '@odata.id': f'/redfish/v1/Managers/{manager_id}',
            'Id': manager_id,
            'Name': f'Manager for {vm_name}',
            'Description': f'BMC for VMware VM {vm_name}',
            'ManagerType': 'BMC',
            'UUID': f'42{vm_name[-8:].ljust(8, "0")}-2938-2342-8820-489239905424',
            'Model': 'VMware vBMC',
            'Manufacturer': 'VMware',
            'FirmwareVersion': '2.0.0',
            'Status': {
                'State': 'Enabled',
                'Health': 'OK'
            },
            'DateTime': datetime.now(timezone.utc).isoformat(),
            'DateTimeLocalOffset': '+00:00',
            'ServiceIdentification': {
                'Product': 'VMware Redfish Server',
                'Vendor': 'VMware'
            },
            'PowerState': 'On',
            'VirtualMedia': {
                '@odata.id': f'/redfish/v1/Managers/{manager_id}/VirtualMedia'
            },
            'EthernetInterfaces': {
                '@odata.id': f'/redfish/v1/Managers/{manager_id}/EthernetInterfaces'
            },
            'Actions': {
                '#Manager.Reset': {
                    'target': f'/redfish/v1/Managers/{manager_id}/Actions/Manager.Reset',
                    'ResetType@Redfish.AllowableValues': [
                        'ForceRestart', 'GracefulRestart'
                    ]
                }
            },
            'Links': {
                'ManagerForSystems': [
                    {
                        '@odata.id': f'/redfish/v1/Systems/{vm_name}'
                    }
                ],
                'ManagerForChassis': [
                    {
                        '@odata.id': f'/redfish/v1/Chassis/{vm_name}-chassis'
                    }
                ]
            }
        }
    
    def _handle_virtual_media_get(self, request_handler, manager_id: str, path: str):
        """Handle VirtualMedia GET requests"""
        # Redfish action URIs (InsertMedia/EjectMedia) only accept POST; return 405 for GET
        if '/Actions/VirtualMedia.InsertMedia' in path or '/Actions/VirtualMedia.EjectMedia' in path:
            self._send_error_response(request_handler, 405, "Method Not Allowed")
            return
        if path.endswith('/VirtualMedia'):
            # VirtualMedia collection
            data = {
                '@odata.type': '#VirtualMediaCollection.VirtualMediaCollection',
                '@odata.id': f'/redfish/v1/Managers/{manager_id}/VirtualMedia',
                'Name': 'Virtual Media Services',
                'Description': f'Virtual Media Services for {manager_id}',
                'Members@odata.count': 2,
                'Members': [
                    {
                        '@odata.id': f'/redfish/v1/Managers/{manager_id}/VirtualMedia/CD'
                    },
                    {
                        '@odata.id': f'/redfish/v1/Managers/{manager_id}/VirtualMedia/Floppy'
                    }
                ]
            }
            self._send_json_response(request_handler, 200, data)
        elif '/VirtualMedia/' in path:
            # Individual virtual media (path may be .../VirtualMedia/CD or .../VirtualMedia/Floppy)
            # Last segment is the media id only if we're not under Actions
            parts = path.rstrip('/').split('/')
            media_id = parts[-1] if parts else ''
            if media_id in ['CD', 'Floppy']:
                data = {
                    '@odata.type': '#VirtualMedia.v1_3_0.VirtualMedia',
                    '@odata.id': f'/redfish/v1/Managers/{manager_id}/VirtualMedia/{media_id}',
                    'Id': media_id,
                    'Name': f'Virtual {media_id}',
                    'Description': f'Virtual {media_id} for {manager_id}',
                    'MediaTypes': ['CD', 'DVD'] if media_id == 'CD' else ['Floppy'],
                    'Connected': False,
                    'Inserted': False,
                    'WriteProtected': True,
                    'ConnectedVia': 'NotConnected',
                    'Actions': {
                        '#VirtualMedia.InsertMedia': {
                            'target': f'/redfish/v1/Managers/{manager_id}/VirtualMedia/{media_id}/Actions/VirtualMedia.InsertMedia'
                        },
                        '#VirtualMedia.EjectMedia': {
                            'target': f'/redfish/v1/Managers/{manager_id}/VirtualMedia/{media_id}/Actions/VirtualMedia.EjectMedia'
                        }
                    }
                }
                self._send_json_response(request_handler, 200, data)
            else:
                self._send_error_response(request_handler, 404, "Virtual media not found")
        else:
            self._send_error_response(request_handler, 404, "Not Found")
    
    def _handle_ethernet_interfaces_get(self, request_handler, manager_id: str, path: str):
        """Handle EthernetInterfaces GET requests"""
        if path.endswith('/EthernetInterfaces'):
            # EthernetInterfaces collection
            data = {
                '@odata.type': '#EthernetInterfaceCollection.EthernetInterfaceCollection',
                '@odata.id': f'/redfish/v1/Managers/{manager_id}/EthernetInterfaces',
                'Name': 'Ethernet Network Interface Collection',
                'Description': f'Ethernet Network Interface Collection for {manager_id}',
                'Members@odata.count': 1,
                'Members': [
                    {
                        '@odata.id': f'/redfish/v1/Managers/{manager_id}/EthernetInterfaces/eth0'
                    }
                ]
            }
            self._send_json_response(request_handler, 200, data)
        elif '/EthernetInterfaces/' in path:
            # Individual ethernet interface
            interface_id = path.split('/')[-1]
            if interface_id == 'eth0':
                data = {
                    '@odata.type': '#EthernetInterface.v1_6_0.EthernetInterface',
                    '@odata.id': f'/redfish/v1/Managers/{manager_id}/EthernetInterfaces/{interface_id}',
                    'Id': interface_id,
                    'Name': 'Management Network Interface',
                    'Description': f'Management Network Interface for {manager_id}',
                    'Status': {
                        'State': 'Enabled',
                        'Health': 'OK'
                    },
                    'InterfaceEnabled': True,
                    'PermanentMACAddress': '00:50:56:84:56:78',
                    'MACAddress': '00:50:56:84:56:78',
                    'SpeedMbps': 1000,
                    'FullDuplex': True,
                    'HostName': f'{manager_id}.local',
                    'FQDN': f'{manager_id}.local',
                    'IPv4Addresses': [
                        {
                            'Address': '192.168.1.100',
                            'SubnetMask': '255.255.255.0',
                            'AddressOrigin': 'Static',
                            'Gateway': '192.168.1.1'
                        }
                    ],
                    'IPv6AddressOriginCounts': {
                        'LinkLocal': 0,
                        'Static': 0,
                        'DHCP': 0,
                        'SLAAC': 0
                    },
                    'IPv6StaticAddresses': [],
                    'NameServers': ['8.8.8.8', '8.8.4.4']
                }
                self._send_json_response(request_handler, 200, data)
            else:
                self._send_error_response(request_handler, 404, "Interface not found")
        else:
            self._send_error_response(request_handler, 404, "Not Found")
    
    def _send_json_response(self, request_handler, status_code: int, data: Dict):
        """Send JSON response"""
        json_data = json.dumps(data, indent=2)
        request_handler.send_response(status_code)
        request_handler.send_header('Content-Type', 'application/json')
        request_handler.send_header('Content-Length', str(len(json_data)))
        request_handler.end_headers()
        request_handler.wfile.write(json_data.encode('utf-8'))
    
    def _send_error_response(self, request_handler, status_code: int, message: str):
        """Send error response"""
        error_data = {
            "error": {
                "code": f"Base.1.0.{status_code}",
                "message": message
            }
        }
        self._send_json_response(request_handler, status_code, error_data)
