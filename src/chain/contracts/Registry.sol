// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract Registry {
    struct Record {
        address submitter;
        uint256 anchoredAt;
        string metadataURI; // pointer to off-chain full JSON (e.g., local path or IPFS CID)
    }

    mapping(bytes32 => Record) public records;

    event RecordAnchored(bytes32 indexed recordHash, address indexed submitter, uint256 timestamp);

    function anchorRecord(bytes32 recordHash, string calldata metadataURI) external {
        require(records[recordHash].anchoredAt == 0, "Record already anchored");
        
        records[recordHash] = Record({
            submitter: msg.sender,
            anchoredAt: block.timestamp,
            metadataURI: metadataURI
        });

        emit RecordAnchored(recordHash, msg.sender, block.timestamp);
    }

    function getRecord(bytes32 recordHash) external view returns (address submitter, uint256 anchoredAt, string memory metadataURI) {
        Record memory record = records[recordHash];
        require(record.anchoredAt != 0, "Record not found");
        
        return (record.submitter, record.anchoredAt, record.metadataURI);
    }
}
