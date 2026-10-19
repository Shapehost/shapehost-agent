# Provider detection sources

Documentation inspected 17 October 2026. No production VM claims are made.

| Adapter | Documented selected fields | Sources / limits |
| --- | --- | --- |
| Hetzner | `/hetzner/v1/metadata/region`, `/availability-zone` on `169.254.169.254` | [Hetzner metadata reference](https://docs.hetzner.cloud/reference/cloud#server-metadata); region is network zone; plan is unknown |
| DigitalOcean | `/metadata/v1/region` on `169.254.169.254` | [Droplet properties](https://docs.digitalocean.com/reference/api/metadata/droplet-properties/); size slug is not in this field, so plan is unknown |
| Vultr | `/v1/region/regioncode` on `169.254.169.254` | [Vultr metadata service](https://www.vultr.com/metadata/), [official metadata overview](https://blogs.vultr.com/Deploy-Dynamic-Instances-Powered-By-Local-Metadata); plan is unknown; no IAM identity token is requested |
| Akamai/Linode | short-lived PUT `/v1/token`, GET `/v1/instance` with `Metadata-Token` and `Accept: application/json` | [Akamai access guide](https://techdocs.akamai.com/cloud-computing/docs/access-the-metadata-service-api), [official SDK](https://github.com/linode/go-metadata); select only `region` and `type`, discard all other JSON fields |
| OVHcloud VPS | no verified authoritative common in-guest endpoint | [OVH VPS catalog documentation](https://help.ovhcloud.com/csm/en-gb-documentation-bare-metal-cloud-virtual-private-servers-advanced-usage?id=kb_browse_cat&kb_category=1a349555f49801102d4ca4d466a7fd07&kb_id=203c4f65551974502d4c6e78b7421996); local vendor string can be a hint only; provider/region/plan stay unknown |

We intentionally do not read a full cloud metadata root, user data, public keys,
account IDs, instance IDs or IP interfaces. Shared OpenStack paths, hostnames,
DMI, ASN and geolocation cannot alone establish provider/location. Multiple
responding adapters produce conflict status. A fixture validates parser behavior,
not availability or authenticity on an actual host. All five launch providers
still require live acceptance against the exact selected OS image and service.

The 18 October 2026 Linode campaign derives its next frozen source cohort from
the existing release by adding only the instance metadata JSON `Accept` header
and its focused regression test. The metadata service otherwise defaults to
plain text, which the JSON detector cannot parse. `SOURCE_COMMIT` uses the
repository Git revision plus the actual agent source-content digest generated
by `scripts/release-agent.ts`; the suffix identifies uncommitted source changes
and does not claim a new upstream release or commit. Earlier frozen campaign
sources and reports retain their original provenance and bytes.
