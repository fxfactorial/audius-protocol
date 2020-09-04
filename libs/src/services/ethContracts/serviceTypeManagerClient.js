const Utils = require('../../utils')
const GovernedContractClient = require('../contracts/GovernedContractClient')
const DEFAULT_GAS_AMOUNT = 200000

class ServiceTypeManagerClient extends GovernedContractClient {
  async setServiceVersion (serviceType, serviceVersion) {
    const method = await this.getGovernedMethod(
      'setServiceVersion',
      Utils.utf8ToHex(serviceType),
      Utils.utf8ToHex(serviceVersion)
    )
    return this.web3Manager.sendTransaction(
      method,
      DEFAULT_GAS_AMOUNT
    )
  }

  async getValidServiceTypes () {
    const method = await this.getMethod('getValidServiceTypes')
    const types = await method.call()
    return types.map(t => Utils.hexToUtf8(t))
  }

  async getCurrentVersion (serviceType) {
    const method = await this.getMethod('getCurrentVersion', Utils.utf8ToHex(serviceType))
    let hexVersion = await method.call()
    return Utils.hexToUtf8(hexVersion)
  }

  async getVersion (serviceType, serviceTypeIndex) {
    let serviceTypeBytes32 = Utils.utf8ToHex(serviceType)
    const method = await this.getMethod('getVersion', serviceTypeBytes32, serviceTypeIndex)
    let version = await method.call()
    return Utils.hexToUtf8(version)
  }

  async getNumberOfVersions (serviceType) {
    const method = await this.getMethod('getNumberOfVersions', Utils.utf8ToHex(serviceType))
    return method.call()
  }

  async getServiceTypeInfo (serviceType) {
    const method = await this.getMethod('getServiceTypeInfo', Utils.utf8ToHex(serviceType))
    return method.call()
  }
}

module.exports = ServiceTypeManagerClient
