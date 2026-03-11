#!/usr/bin/env python3
"""
Authentication Module
Handles Redfish authentication and session management.
"""

import base64
import logging
import time
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class AuthenticationManager:
    """Manages authentication and sessions for Redfish server"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.sessions = {}
        self.session_timeout = 600  # 10 minutes
        logger.info("🔐 Authentication manager initialized")
    
    def authenticate_request(self, request_handler) -> Tuple[bool, Optional[str]]:
        """
        Authenticate incoming request.
        Supports: Basic auth, Bearer (session token), and X-Auth-Token (Redfish/Metal3 session token).

        Args:
            request_handler: HTTP request handler

        Returns:
            Tuple of (is_authenticated, username)
        """
        auth_header = request_handler.headers.get('Authorization')
        x_auth_token = request_handler.headers.get('X-Auth-Token')

        # X-Auth-Token: Redfish session token (used by Metal3/Ironic after session creation)
        if x_auth_token and x_auth_token.strip():
            valid, username = self._validate_session_token(x_auth_token.strip())
            if valid:
                return True, username

        if not auth_header:
            logger.debug("🔒 No authorization header found")
            return False, None

        try:
            if auth_header.startswith('Basic '):
                # Basic Authentication
                encoded_credentials = auth_header[6:]
                decoded_credentials = base64.b64decode(encoded_credentials).decode('utf-8')
                username, password = decoded_credentials.split(':', 1)
                
                logger.debug(f"🔑 Basic auth attempt for user: {username}")
                
                # Check credentials (using default admin/password for now)
                if username == 'admin' and password == 'password':
                    logger.info(f"✅ Basic authentication successful for: {username}")
                    return True, username
                else:
                    logger.warning(f"❌ Basic authentication failed for: {username}")
                    return False, None
                    
            elif auth_header.startswith('Bearer '):
                # Session Token Authentication
                token = auth_header[7:]
                return self._validate_session_token(token)
                
        except Exception as e:
            logger.error(f"❌ Authentication error: {e}")
            return False, None
        
        logger.debug("🔒 Unsupported authentication method")
        return False, None
    
    def create_session(self, username: str) -> Dict:
        """Create a new session for authenticated user"""
        import uuid
        
        session_id = str(uuid.uuid4())
        session_token = str(uuid.uuid4())
        
        session_data = {
            'Id': session_id,
            'Name': f'Session for {username}',
            'Description': f'Active session for user {username}',
            'UserName': username,
            'Token': session_token,
            'CreatedTime': time.time(),
            'LastAccessTime': time.time()
        }
        
        self.sessions[session_id] = session_data
        logger.info(f"🎫 Session created for user: {username} (ID: {session_id})")
        
        return {
            'Id': session_id,
            'SessionToken': session_token,
            'Uri': f'/redfish/v1/SessionService/Sessions/{session_id}'
        }
    
    def _validate_session_token(self, token: str) -> Tuple[bool, Optional[str]]:
        """Validate session token"""
        current_time = time.time()
        
        for session_id, session_data in self.sessions.items():
            if session_data.get('Token') == token:
                # Check if session expired
                if current_time - session_data['LastAccessTime'] > self.session_timeout:
                    logger.warning(f"🕐 Session expired for: {session_data['UserName']}")
                    del self.sessions[session_id]
                    return False, None
                
                # Update last access time
                session_data['LastAccessTime'] = current_time
                logger.debug(f"✅ Valid session token for: {session_data['UserName']}")
                return True, session_data['UserName']
        
        logger.warning("❌ Invalid session token")
        return False, None
    
    def delete_session(self, session_id: str) -> bool:
        """Delete a session"""
        if session_id in self.sessions:
            username = self.sessions[session_id]['UserName']
            del self.sessions[session_id]
            logger.info(f"🗑️ Session deleted for user: {username} (ID: {session_id})")
            return True
        return False
    
    def get_session(self, session_id: str) -> Optional[Dict]:
        """Get session data"""
        return self.sessions.get(session_id)
    
    def list_sessions(self) -> Dict:
        """List all active sessions"""
        return {
            '@odata.type': '#SessionCollection.SessionCollection',
            '@odata.id': '/redfish/v1/SessionService/Sessions',
            'Name': 'Session Collection',
            'Description': 'Active Sessions',
            'Members@odata.count': len(self.sessions),
            'Members': [
                {
                    '@odata.id': f'/redfish/v1/SessionService/Sessions/{session_id}'
                }
                for session_id in self.sessions.keys()
            ]
        }
    
    def cleanup_expired_sessions(self):
        """Remove expired sessions"""
        current_time = time.time()
        expired_sessions = []
        
        for session_id, session_data in self.sessions.items():
            if current_time - session_data['LastAccessTime'] > self.session_timeout:
                expired_sessions.append(session_id)
        
        for session_id in expired_sessions:
            username = self.sessions[session_id]['UserName']
            del self.sessions[session_id]
            logger.info(f"🧹 Expired session cleaned up for: {username} (ID: {session_id})")
