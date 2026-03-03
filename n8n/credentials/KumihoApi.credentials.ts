import type { ICredentialTestRequest, ICredentialType, INodeProperties, Icon } from 'n8n-workflow';

export class KumihoApi implements ICredentialType {
  name = 'kumihoApi';
  displayName = 'Kumiho API';
  documentationUrl = 'https://kumiho.io/resources/n8n';
  icon: Icon = 'file:kumiho.svg';
  test: ICredentialTestRequest = {
    request: {
      method: 'GET',
      url: '={{$credentials.baseUrl}}/api/v1/tenant/whoami',
      headers: {
        'X-Kumiho-Token': '={{$credentials.serviceToken}}',
        'x-tenant-id': '={{$credentials.tenantId}}',
      },
    },
  };
  properties: INodeProperties[] = [
    {
      displayName: 'Base URL',
      name: 'baseUrl',
      type: 'string',
      default: 'https://api.kumiho.cloud'
    },
    {
      displayName: 'Service Token',
      name: 'serviceToken',
      type: 'string',
      typeOptions: {
        password: true
      },
      default: ''
    },
    {
      displayName: 'Tenant ID (Optional)',
      name: 'tenantId',
      type: 'string',
      default: '',
      description: 'If your token does not contain a tenant_id claim, set this to your tenant id/slug so requests can be routed correctly.',
    },
    {
      displayName: 'User Token (Optional)',
      name: 'userToken',
      type: 'string',
      typeOptions: {
        password: true,
      },
      default: '',
      description: 'Optional end-user ID token (Authorization: Bearer ...) for user-scoped endpoints. Leave empty for service-token-only usage.',
    }
  ];
}
