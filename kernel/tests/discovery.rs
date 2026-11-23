//! Directory bounds remain independent of the frozen per-descriptor node budget.

use serde_json::{Value, json};
use skulk_capability_kernel::{discovery, portable_v2};

fn directory(count: usize, rich: bool) -> Vec<u8> {
    let mut offer: Value =
        serde_json::from_str(include_str!("../contracts/discovery-offer.json")).unwrap();
    if rich {
        offer["descriptor"]["input_schema"] = json!({"type":"object","properties":{"selection":{"enum": (0..512).collect::<Vec<_>>()}}});
        offer["target"]["descriptor_revision"] = json!(
            portable_v2::contract_digest("Descriptor", &offer["descriptor"].to_string()).unwrap()
        );
    }
    let values: Vec<_> = (0..count)
        .map(|index| {
            let mut item = offer.clone();
            item["plugin_id"] = json!(format!("managed.example{index}"));
            item
        })
        .collect();
    serde_json::to_vec(&values).unwrap()
}

#[test]
fn valid_directories_do_not_share_one_descriptor_node_budget() {
    let fixture: Value =
        serde_json::from_str(include_str!("../contracts/discovery-v1-cases.json")).unwrap();
    let request = &fixture["cases"][0]["request"];
    let startup = request["startup"].as_str().unwrap();
    let context = request["context"].as_str().unwrap();
    let query =
        discovery::request(startup, context, request["parent"].as_str().unwrap(), 1).unwrap();
    let metadata = std::str::from_utf8(query.metadata()).unwrap();
    let mut nested = json!(0);
    for _ in 0..62 {
        nested = json!([nested]);
    }
    let mut deep: Value =
        serde_json::from_str(include_str!("../contracts/discovery-offer.json")).unwrap();
    deep["descriptor"]["input_schema"]["default"] = nested;
    deep["target"]["descriptor_revision"] =
        json!(portable_v2::contract_digest("Descriptor", &deep["descriptor"].to_string()).unwrap());
    for body in [
        directory(64, true),
        directory(1000, false),
        serde_json::to_vec(&vec![deep]).unwrap(),
    ] {
        assert!(body.len() < discovery::MAX_PAYLOAD_BYTES);
        let reply = discovery::reply(startup, metadata, body.clone(), None).unwrap();
        let (error, actual) =
            discovery::response(startup, metadata, &discovery::encode(&reply)).unwrap();
        assert!(error.is_none());
        assert_eq!(actual, body);
    }
    assert!(discovery::reply(startup, metadata, directory(4097, false), None).is_err());
}
