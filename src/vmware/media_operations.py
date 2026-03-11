#!/usr/bin/env python3
"""
VMware Virtual Media Operations
Handles ISO mounting, boot order, and virtual media operations.
"""

import logging
import os
import tempfile
import time
from urllib.parse import urlparse

import requests
from pyVmomi import vim

logger = logging.getLogger(__name__)

# Default subfolder on the datastore for Redfish-mounted ISOs (when virtual_media_folder not set)
REDFISH_ISO_FOLDER = "redfish-isos"


class InsertMediaError(Exception):
    """Raised when VirtualMedia.InsertMedia fails; message is safe to return to client."""
    pass


class MediaOperations:
    """Virtual media and boot operations"""
    
    def __init__(self, connection, vm_operations):
        """
        Initialize media operations
        
        Args:
            connection: VMwareConnection instance
            vm_operations: VMOperations instance
        """
        self.connection = connection
        self.vm_operations = vm_operations
    
    def set_vm_boot_order(self, vm_name, boot_order):
        """
        Set VM boot order
        
        Args:
            vm_name: Name of the virtual machine
            boot_order: List of boot devices ['cdrom', 'disk', 'network']
            
        Returns:
            True if successful, False otherwise
        """
        try:
            vm = self.vm_operations.get_vm(vm_name)
            if not vm:
                logger.error(f"VM '{vm_name}' not found")
                return False
            
            logger.info(f"Setting boot order for VM '{vm_name}': {boot_order}")
            
            # Create boot options
            boot_options = []
            for device in boot_order:
                if device.lower() == 'cdrom':
                    boot_options.append(vim.vm.BootOptions.BootableCdromDevice())
                elif device.lower() == 'disk':
                    boot_options.append(vim.vm.BootOptions.BootableDiskDevice())
                elif device.lower() == 'network':
                    boot_options.append(vim.vm.BootOptions.BootableEthernetDevice())
            
            # Configure boot options
            boot_spec = vim.vm.BootOptions()
            boot_spec.bootOrder = boot_options
            
            config_spec = vim.vm.ConfigSpec()
            config_spec.bootOptions = boot_spec
            
            task = vm.Reconfigure(config_spec)
            result = self._wait_for_task(task)
            
            if result:
                logger.info(f"Successfully set boot order for VM '{vm_name}'")
            else:
                logger.error(f"Failed to set boot order for VM '{vm_name}'")
            
            return result
            
        except Exception as e:
            logger.error(f"Error setting boot order for VM '{vm_name}': {e}")
            return False
    
    def mount_iso(self, vm_name, iso_path):
        """
        Mount ISO to VM's CD/DVD drive
        
        Args:
            vm_name: Name of the virtual machine
            iso_path: Path to the ISO file on the datastore
            
        Returns:
            True if successful, False otherwise
        """
        try:
            vm = self.vm_operations.get_vm(vm_name)
            if not vm:
                logger.error(f"VM '{vm_name}' not found")
                return False
            
            logger.info(f"Mounting ISO '{iso_path}' to VM '{vm_name}'")
            
            # Find CD/DVD device
            cdrom_device = None
            for device in vm.config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualCdrom):
                    cdrom_device = device
                    break
            
            if not cdrom_device:
                logger.error(f"No CD/DVD device found for VM '{vm_name}'")
                return False
            
            # Connectable type: pyVmomi uses VirtualDevice.ConnectInfo (not ConnectableDevice)
            connectable = self._make_connectable(cdrom_device, connected=True, start_connected=True)
            if not connectable:
                return False
            
            # Configure CD/DVD device to use ISO
            cdrom_spec = vim.vm.device.VirtualDeviceSpec()
            cdrom_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
            cdrom_spec.device = cdrom_device
            cdrom_spec.device.backing = vim.vm.device.VirtualCdrom.IsoBackingInfo()
            cdrom_spec.device.backing.fileName = iso_path
            cdrom_spec.device.connectable = connectable
            
            config_spec = vim.vm.ConfigSpec()
            config_spec.deviceChange = [cdrom_spec]
            
            task = vm.Reconfigure(config_spec)
            result = self._wait_for_task(task)
            
            if result:
                logger.info(f"Successfully mounted ISO '{iso_path}' to VM '{vm_name}'")
            else:
                logger.error(f"Failed to mount ISO '{iso_path}' to VM '{vm_name}'")
            
            return result
            
        except Exception as e:
            logger.error(f"Error mounting ISO to VM '{vm_name}': {e}")
            return False
    
    def unmount_iso(self, vm_name):
        """
        Unmount ISO from VM's CD/DVD drive
        
        Args:
            vm_name: Name of the virtual machine
            
        Returns:
            True if successful, False otherwise
        """
        try:
            vm = self.vm_operations.get_vm(vm_name)
            if not vm:
                logger.error(f"VM '{vm_name}' not found")
                return False
            
            logger.info(f"Unmounting ISO from VM '{vm_name}'")
            
            # Find CD/DVD device
            cdrom_device = None
            for device in vm.config.hardware.device:
                if isinstance(device, vim.vm.device.VirtualCdrom):
                    cdrom_device = device
                    break
            
            if not cdrom_device:
                logger.error(f"No CD/DVD device found for VM '{vm_name}'")
                return False
            
            connectable = self._make_connectable(cdrom_device, connected=False, start_connected=False)
            if not connectable:
                return False
            
            # Configure CD/DVD device to disconnect
            cdrom_spec = vim.vm.device.VirtualDeviceSpec()
            cdrom_spec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
            cdrom_spec.device = cdrom_device
            cdrom_spec.device.backing = vim.vm.device.VirtualCdrom.RemotePassthroughBackingInfo()
            cdrom_spec.device.connectable = connectable
            
            config_spec = vim.vm.ConfigSpec()
            config_spec.deviceChange = [cdrom_spec]
            
            task = vm.Reconfigure(config_spec)
            result = self._wait_for_task(task)
            
            if result:
                logger.info(f"Successfully unmounted ISO from VM '{vm_name}'")
            else:
                logger.error(f"Failed to unmount ISO from VM '{vm_name}'")
            
            return result
            
        except Exception as e:
            logger.error(f"Error unmounting ISO from VM '{vm_name}': {e}")
            return False
    
    def _make_connectable(self, cdrom_device, connected=True, start_connected=True):
        """Build a VirtualDevice connectable for CD/DVD. Works across pyVmomi versions (ConnectInfo vs ConnectableDevice)."""
        try:
            # Prefer type from existing device (matches this vCenter/pyVmomi)
            if getattr(cdrom_device, "connectable", None) is not None:
                conn_type = type(cdrom_device.connectable)
                c = conn_type()
            elif hasattr(vim.vm.device.VirtualDevice, "ConnectInfo"):
                # pyVmomi: vSphere API VirtualDeviceConnectInfo is exposed as ConnectInfo
                c = vim.vm.device.VirtualDevice.ConnectInfo()
            elif hasattr(vim.vm.device.VirtualDevice, "ConnectableDevice"):
                c = vim.vm.device.VirtualDevice.ConnectableDevice()
            else:
                logger.error("Cannot create connectable: VirtualDevice has no ConnectInfo/ConnectableDevice or device.connectable")
                return None
            c.connected = connected
            c.startConnected = start_connected
            if hasattr(c, "allowGuestControl"):
                c.allowGuestControl = True
            return c
        except Exception as e:
            logger.error(f"Failed to create connectable: {e}", exc_info=True)
            return None

    def _get_datastore_by_name(self, content, datastore_name):
        """Return (datastore, datacenter) for the given datastore name, or (None, None)."""
        if not datastore_name or not content:
            return None, None
        try:
            for dc in content.rootFolder.childEntity:
                if not hasattr(dc, 'datastoreFolder') or not dc.datastoreFolder:
                    continue
                for ds in dc.datastoreFolder.childEntity:
                    if hasattr(ds, 'info') and ds.info.name == datastore_name.strip():
                        return ds, dc
        except Exception as e:
            logger.debug(f"_get_datastore_by_name: {e}")
        return None, None

    def mount_iso_from_url(self, vm_name, image_url, write_protected=True,
                           datastore_name=None, virtual_media_folder=None):
        """
        Download an image from a URL, upload it to a datastore, and mount as CD.
        Used by Redfish VirtualMedia.InsertMedia (e.g. Metal3/Ironic agent or install ISO).
        
        Args:
            vm_name: Name of the virtual machine
            image_url: HTTP(S) URL of the ISO image
            write_protected: Ignored for VMware; kept for Redfish API compatibility
            datastore_name: Optional. Datastore name to upload to (e.g. "isos"). If not set, uses VM's datastore.
            virtual_media_folder: Optional. Folder path on the datastore. May contain {vm_name} and {filename}.
                                 Examples: "arolivei" -> arolivei/<filename>;
                                          "redfish-isos/{vm_name}" (default) -> redfish-isos/<vm_name>/<filename>.
        
        Returns:
            True if successful, False otherwise
        """
        if not image_url or not image_url.strip():
            raise InsertMediaError("Image URL is empty")
        parsed = urlparse(image_url)
        if parsed.scheme not in ('http', 'https'):
            raise InsertMediaError(f"Unsupported image URL scheme: {parsed.scheme}")
        try:
            vm = self.vm_operations.get_vm(vm_name)
            if not vm:
                raise InsertMediaError(f"VM '{vm_name}' not found")
            content = self.connection.get_content()
            if not content:
                raise InsertMediaError("vSphere content unavailable")
            if datastore_name:
                datastore, dc = self._get_datastore_by_name(content, datastore_name)
                if not datastore or not dc:
                    raise InsertMediaError(f"Datastore '{datastore_name}' not found")
            else:
                if not vm.datastore:
                    raise InsertMediaError(f"VM '{vm_name}' has no datastore")
                datastore = vm.datastore[0]
                dc = self._get_datacenter_for_datastore(content, datastore)
                if not dc:
                    raise InsertMediaError(f"Could not find datacenter for datastore '{datastore.info.name}'")
            filename = os.path.basename(parsed.path) or f"redfish-{int(time.time())}.iso"
            if not filename.lower().endswith('.iso'):
                filename = filename + ".iso"
            folder_template = (virtual_media_folder or f"{REDFISH_ISO_FOLDER}/{{vm_name}}").rstrip("/")
            try:
                folder_path = folder_template.format(vm_name=vm_name, filename=filename)
            except KeyError as e:
                raise InsertMediaError(f"virtual_media_folder contains unsupported placeholder: {e}")
            remote_path = f"{folder_path}/{filename}"
            logger.info(f"InsertMedia: downloading from {image_url[:80]}... for VM '{vm_name}' (upload to [{datastore.info.name}] {remote_path})")
            # Skip SSL verification for the Image URL: Metal3/Ironic often serve the ISO over HTTPS with a self-signed cert
            resp = requests.get(image_url, stream=True, timeout=300, verify=False)
            resp.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".iso") as tmp:
                for chunk in resp.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        tmp.write(chunk)
                tmp_path = tmp.name
            try:
                ok, err = self._upload_file_to_datastore(vm_name, tmp_path, remote_path, datastore, dc)
                if not ok:
                    raise InsertMediaError(err or "Datastore upload failed")
                iso_path = f"[{datastore.info.name}] {remote_path}"
                if not self.mount_iso(vm_name, iso_path):
                    raise InsertMediaError("Mount ISO failed")
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        except InsertMediaError:
            raise
        except requests.RequestException as e:
            logger.error(f"InsertMedia download failed: {e}")
            raise InsertMediaError(f"Download failed: {e}") from e
        except Exception as e:
            logger.error(f"InsertMedia error for VM '{vm_name}': {e}", exc_info=True)
            raise InsertMediaError(str(e)) from e
    
    def _get_datacenter_for_datastore(self, content, datastore):
        """Return the datacenter that contains the given datastore, or None."""
        try:
            for dc in content.rootFolder.childEntity:
                if not hasattr(dc, 'datastoreFolder') or not dc.datastoreFolder:
                    continue
                for ds in dc.datastoreFolder.childEntity:
                    if ds == datastore or (hasattr(ds, 'info') and ds.info.name == datastore.info.name):
                        return dc
        except Exception as e:
            logger.debug(f"_get_datacenter_for_datastore: {e}")
        return None
    
    def _upload_file_to_datastore(self, vm_name, local_path, remote_path, datastore, datacenter):
        """Upload a local file to the given datastore. Returns (True, None) on success, (False, error_message) on failure."""
        try:
            si = self.connection.get_service_instance()
            if not si:
                return False, "No vSphere service instance"
            if not remote_path.startswith("/"):
                remote_path = "/" + remote_path
            resource = "/folder" + remote_path
            params = {"dsName": datastore.info.name, "dcPath": datacenter.name}
            port = getattr(self.connection, 'port', 443)
            scheme = "https"
            http_url = f"{scheme}://{self.connection.host}:{port}{resource}"
            cookie = getattr(si._stub, 'cookie', None)
            if not cookie:
                return False, "No session cookie for datastore upload (vCenter auth)"
            cookie_name = cookie.split("=", 1)[0]
            cookie_value = cookie.split("=", 1)[1].split(";", 1)[0]
            cookie_path = cookie.split("=", 1)[1].split(";", 1)[1].split(";", 1)[0].lstrip()
            cookie_text = " " + cookie_value + "; $" + cookie_path
            cookies = {cookie_name: cookie_text}
            # Upload is to the same vCenter host we already connected to via pyVmomi (which
            # does not verify certs). Skip SSL verify here to avoid SSLError on self-signed vCenter.
            headers = {"Content-Type": "application/octet-stream"}
            with open(local_path, "rb") as f:
                r = requests.put(
                    http_url, params=params, data=f, headers=headers,
                    cookies=cookies, verify=False, timeout=600)
            if r.status_code not in (200, 201, 204):
                msg = r.text[:300] if r.text else r.reason or ""
                logger.error(f"Datastore upload HTTP {r.status_code}: {msg}")
                return False, f"Datastore upload failed: HTTP {r.status_code}. {msg}"
            logger.info(f"Uploaded to [{datastore.info.name}] {remote_path}")
            return True, None
        except Exception as e:
            logger.error(f"Upload to datastore failed: {e}", exc_info=True)
            return False, str(e)
    
    def _wait_for_task(self, task):
        """
        Wait for a vCenter task to complete
        
        Args:
            task: Task object
            
        Returns:
            True if task completed successfully, False otherwise
        """
        try:
            import time
            while task.info.state in ['running', 'queued']:
                time.sleep(1)
            
            if task.info.state == 'success':
                return True
            else:
                logger.error(f"Task failed: {task.info.error}")
                return False
                
        except Exception as e:
            logger.error(f"Error waiting for task: {e}")
            return False
