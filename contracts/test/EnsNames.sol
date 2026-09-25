// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

/// Test helpers: ENS namehash (ENSIP-1) and DNS wire encoding for dot-separated names.
library EnsNames {
    function _labels(string memory name) private pure returns (bytes[] memory out) {
        bytes memory b = bytes(name);
        uint256 count = 1;
        for (uint256 i; i < b.length; ++i) if (b[i] == ".") count++;
        out = new bytes[](count);
        uint256 start;
        uint256 k;
        for (uint256 i; i <= b.length; ++i) {
            if (i == b.length || b[i] == ".") {
                bytes memory label = new bytes(i - start);
                for (uint256 j; j < label.length; ++j) label[j] = b[start + j];
                out[k++] = label;
                start = i + 1;
            }
        }
    }

    function namehash(string memory name) internal pure returns (bytes32 node) {
        bytes[] memory labels = _labels(name);
        for (uint256 i = labels.length; i > 0; --i) {
            node = keccak256(abi.encodePacked(node, keccak256(labels[i - 1])));
        }
    }

    function dnsEncode(string memory name) internal pure returns (bytes memory out) {
        bytes[] memory labels = _labels(name);
        for (uint256 i; i < labels.length; ++i) {
            out = abi.encodePacked(out, uint8(labels[i].length), labels[i]);
        }
        out = abi.encodePacked(out, uint8(0));
    }
}
