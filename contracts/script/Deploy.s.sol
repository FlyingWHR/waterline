// SPDX-License-Identifier: MIT
pragma solidity ^0.8.25;

import {Script, console} from "forge-std/Script.sol";
import {Marks} from "../src/Marks.sol";

interface IMintable {
    function mint(address to, uint256 amount) external;
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IETHRegistrar {
    function makeCommitment(string calldata, address, bytes32, address, address, uint64, bytes32)
        external
        pure
        returns (bytes32);
    function commit(bytes32) external;
    function register(string calldata, address, bytes32, address, address, uint64, address, bytes32)
        external
        returns (uint256);
    function isAvailable(string calldata) external view returns (bool);
}

/// Step 1: deploy Marks and commit to the name.
///   forge script script/Deploy.s.sol:DeployAndCommit --rpc-url sepolia --broadcast
/// Step 2 (at least 60 s later): register the name with Marks as its resolver.
///   forge script script/Deploy.s.sol:Register --rpc-url sepolia --broadcast
/// Env: DEPLOYER_KEY (admin + name owner), REPORTER_ADDRESS (the Waterline API's account), NAME_SECRET (any 0x32 bytes).
abstract contract Base is Script {
    uint64 constant DURATION = 365 days;

    function cfg() internal view returns (string memory) {
        return vm.readFile("./ens.sepolia.json");
    }

    function registrar() internal view returns (IETHRegistrar) {
        return IETHRegistrar(vm.parseJsonAddress(cfg(), ".ETHRegistrar"));
    }

    function label() internal view returns (string memory) {
        return vm.envOr("NAME_LABEL", vm.parseJsonString(cfg(), ".label"));
    }
}

contract DeployAndCommit is Base {
    function run() external {
        uint256 key = vm.envUint("DEPLOYER_KEY");
        address owner = vm.addr(key);
        require(registrar().isAvailable(label()), "name is taken: set NAME_LABEL");
        vm.startBroadcast(key);
        Marks marks = new Marks(owner, vm.envAddress("REPORTER_ADDRESS"));
        registrar().commit(
            registrar().makeCommitment(
                label(), owner, vm.envBytes32("NAME_SECRET"), address(0), address(marks), DURATION, 0
            )
        );
        vm.stopBroadcast();
        vm.writeJson(
            string.concat('{"marks":"', vm.toString(address(marks)), '","label":"', label(), '","owner":"', vm.toString(owner), '"}'),
            "./deployments/sepolia.json"
        );
        console.log("Marks", address(marks));
        console.log("Committed; run Register after 60 seconds.");
    }
}

contract Register is Base {
    function run() external {
        uint256 key = vm.envUint("DEPLOYER_KEY");
        address owner = vm.addr(key);
        address marks = vm.parseJsonAddress(vm.readFile("./deployments/sepolia.json"), ".marks");
        IMintable usdc = IMintable(vm.parseJsonAddress(cfg(), ".MockUSDC"));
        vm.startBroadcast(key);
        usdc.mint(owner, 100e6); // MockUSDC has a public mint on Sepolia
        usdc.approve(address(registrar()), 100e6);
        registrar().register(label(), owner, vm.envBytes32("NAME_SECRET"), address(0), marks, DURATION, address(usdc), 0);
        vm.stopBroadcast();
        console.log(string.concat(label(), ".eth now resolves through Marks"));
    }
}
